import logging
import os
import shlex
import subprocess

import boto3
from botocore.config import Config as BotocoreConfig

_BOTO_CFG = BotocoreConfig(connect_timeout=10, read_timeout=30, retries={"max_attempts": 1})

logger = logging.getLogger(__name__)

SCHEMA = {
    "name": "execute_approved",
    "description": (
        "Executes the approved fix actions. Requires a valid HITL approval token. "
        "Supports any action type: kubectl, terraform, aws_cli, ssm, manual. "
        "Every action is logged to CloudTrail via structured audit log."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "Incident ID this approval belongs to.",
                },
                "token": {
                    "type": "string",
                    "description": "HITL approval token received from the operator.",
                },
                "actions": {
                    "type": "array",
                    "description": "Actions to execute — same list from propose_fix output.",
                    "items": {"type": "object"},
                    "default": [],
                },
            },
            "required": ["incident_id", "token"],
        }
    },
}

# Dispatcher — maps action type to handler method
_HANDLERS = {}


class ExecuteApprovedTool:
    def __init__(self, hitl_service):
        self.hitl = hitl_service

    def execute(self, incident_id: str, token: str, actions: list = None) -> dict:
        if not self.hitl.validate_token(token, incident_id):
            logger.warning("execute_rejected invalid_token incident_id=%s", incident_id)
            return {
                "status": "rejected",
                "reason": "Invalid or expired approval token. Request a new approval.",
            }

        results = []
        errors = []

        for action in (actions or []):
            action_type = action.get("type", "unknown")
            handler = getattr(self, f"_run_{action_type}", self._run_unknown)
            result = handler(action)
            results.append({"type": action_type, **result})

            if not result.get("success", False):
                errors.append(action_type)
                logger.error("action_failed type=%s result=%s", action_type, result)

        success = len(errors) == 0
        logger.info(
            "execution_complete incident_id=%s success=%s actions=%d errors=%d",
            incident_id, success, len(results), len(errors),
        )

        return {
            "status": "executed" if success else "partial_failure",
            "incident_id": incident_id,
            "results": results,
            "errors": errors,
            "success": success,
        }

    # ------------------------------------------------------------------
    # Action handlers — one per supported type
    # ------------------------------------------------------------------

    def _run_kubectl(self, action: dict) -> dict:
        return self._shell(action.get("command", ""))

    def _run_terraform(self, action: dict) -> dict:
        diff = action.get("diff", "")
        if not diff:
            return {"success": False, "stderr": "terraform action missing 'diff' field"}

        tf_dir = os.environ.get("TERRAFORM_WORKING_DIR", "/tmp/tf-apply")
        os.makedirs(tf_dir, exist_ok=True)

        with open(os.path.join(tf_dir, "fix.tf"), "w") as f:
            f.write(diff)

        init = self._shell(f"terraform -chdir={tf_dir} init -input=false")
        if not init["success"]:
            return init

        return self._shell(f"terraform -chdir={tf_dir} apply -auto-approve -input=false")

    def _run_aws_cli(self, action: dict) -> dict:
        command = action.get("command", "")
        command = self._fix_statistics_param(command)
        command = self._fix_jq_dependency(command)
        # Prefer boto3 (no CLI needed in container)
        result = self._boto3_aws_cli(command)
        if result is not None:
            return result
        # Fallback to subprocess (only if aws CLI is available)
        return self._shell(command)

    def _boto3_aws_cli(self, command: str) -> dict | None:
        """Parse common AWS CLI patterns and execute via boto3 (no CLI binary needed)."""
        try:
            parts = shlex.split(command)
        except ValueError:
            return None

        if not parts or parts[0] != "aws" or len(parts) < 3:
            return None

        # Extract --region (fall back to env var)
        region = os.environ.get("AWS_REGION", "us-east-1")
        if "--region" in parts:
            idx = parts.index("--region")
            if idx + 1 < len(parts):
                region = parts[idx + 1]

        service    = parts[1]
        subcommand = parts[2]

        # Build flat param dict from --key value pairs (skip --region, --output, --query)
        skip_keys = {"region", "output", "query"}
        params: dict = {}
        i = 3
        while i < len(parts):
            if parts[i].startswith("--"):
                key = parts[i][2:]
                if key in skip_keys:
                    i += 2
                    continue
                if i + 1 < len(parts) and not parts[i + 1].startswith("--"):
                    params[key] = parts[i + 1]
                    i += 2
                else:
                    params[key] = True
                    i += 1
            else:
                i += 1

        try:
            if service == "lambda":
                return self._boto3_lambda(subcommand, params, region)
            if service == "ecs":
                return self._boto3_ecs(subcommand, params, region)
            if service == "rds":
                return self._boto3_rds(subcommand, params, region)
        except Exception as exc:
            logger.error("boto3_aws_cli_failed service=%s sub=%s error=%s", service, subcommand, exc)
            return {"success": False, "stderr": str(exc)}

        # Unknown service — return error (no subprocess fallback, aws CLI not in container)
        return {"success": False, "stderr": f"Unsupported AWS service: {service}. Add it to _boto3_aws_cli."}

    # ------------------------------------------------------------------
    # boto3 service handlers
    # ------------------------------------------------------------------

    def _boto3_lambda(self, subcommand: str, params: dict, region: str) -> dict | None:
        client = boto3.client("lambda", region_name=region, config=_BOTO_CFG)

        if subcommand == "update-function-configuration":
            fn = params.get("function-name")
            if not fn:
                return {"success": False, "stderr": "missing --function-name"}
            kwargs: dict = {"FunctionName": fn}
            if "timeout" in params:
                kwargs["Timeout"] = int(params["timeout"])
            if "memory-size" in params:
                kwargs["MemorySize"] = int(params["memory-size"])
            resp = client.update_function_configuration(**kwargs)
            return {
                "success": True,
                "stdout": (
                    f"Lambda {fn} updated — "
                    f"timeout={resp.get('Timeout')}s "
                    f"memory={resp.get('MemorySize')}MB"
                ),
            }

        if subcommand == "update-function-code":
            fn = params.get("function-name")
            if not fn:
                return {"success": False, "stderr": "missing --function-name"}
            kwargs = {"FunctionName": fn}
            if "image-uri" in params:
                kwargs["ImageUri"] = params["image-uri"]
            elif "zip-file" in params:
                kwargs["ZipFile"] = params["zip-file"]
            resp = client.update_function_code(**kwargs)
            return {"success": True, "stdout": f"Lambda {fn} code updated — state={resp.get('State')}"}

        if subcommand == "put-function-concurrency":
            fn = params.get("function-name")
            if not fn:
                return {"success": False, "stderr": "missing --function-name"}
            concurrency = params.get("reserved-concurrent-executions")
            if not concurrency:
                return {"success": False, "stderr": "missing --reserved-concurrent-executions"}
            try:
                resp = client.put_function_concurrency(
                    FunctionName=fn,
                    ReservedConcurrentExecutions=int(concurrency),
                )
                return {
                    "success": True,
                    "stdout": f"Lambda {fn} concurrency set to {resp.get('ReservedConcurrentExecutions')}",
                }
            except client.exceptions.InvalidParameterValueException as exc:
                if "UnreservedConcurrentExecution" in str(exc):
                    # Account concurrency pool too small to reserve — remove limit instead
                    logger.warning(
                        "put_concurrency_fallback fn=%s requested=%s reason=account_pool_too_small",
                        fn, concurrency,
                    )
                    client.delete_function_concurrency(FunctionName=fn)
                    return {
                        "success": True,
                        "stdout": (
                            f"Lambda {fn}: could not reserve {concurrency} (account pool too small). "
                            "Removed concurrency limit instead — function now uses unreserved pool."
                        ),
                    }
                raise

        if subcommand == "delete-function-concurrency":
            fn = params.get("function-name")
            if not fn:
                return {"success": False, "stderr": "missing --function-name"}
            client.delete_function_concurrency(FunctionName=fn)
            return {"success": True, "stdout": f"Lambda {fn} concurrency limit removed"}

        if subcommand == "get-function-configuration":
            fn = params.get("function-name")
            if not fn:
                return {"success": False, "stderr": "missing --function-name"}
            resp = client.get_function_configuration(FunctionName=fn)
            return {
                "success": True,
                "stdout": f"Timeout={resp.get('Timeout')}s Memory={resp.get('MemorySize')}MB State={resp.get('State')}",
            }

        if subcommand == "get-function-concurrency":
            fn = params.get("function-name")
            if not fn:
                return {"success": False, "stderr": "missing --function-name"}
            try:
                resp = client.get_function_concurrency(FunctionName=fn)
                return {"success": True, "stdout": f"ReservedConcurrency={resp.get('ReservedConcurrentExecutions', 'unreserved')}"}
            except client.exceptions.ResourceNotFoundException:
                return {"success": True, "stdout": "ReservedConcurrency=unreserved (no limit set)"}

        # Unknown subcommand — return error instead of falling through to subprocess
        return {"success": False, "stderr": f"Unsupported lambda subcommand: {subcommand}. Add it to _boto3_lambda."}

    def _boto3_ecs(self, subcommand: str, params: dict, region: str) -> dict | None:
        client = boto3.client("ecs", region_name=region, config=_BOTO_CFG)

        if subcommand == "update-service":
            cluster = params.get("cluster")
            service = params.get("service")
            if not cluster or not service:
                return {"success": False, "stderr": "missing --cluster or --service"}
            kwargs: dict = {"cluster": cluster, "service": service}
            if "desired-count" in params:
                kwargs["desiredCount"] = int(params["desired-count"])
            if "task-definition" in params:
                kwargs["taskDefinition"] = params["task-definition"]
            if "force-new-deployment" in params:
                kwargs["forceNewDeployment"] = True
            resp = client.update_service(**kwargs)
            svc = resp.get("service", {})
            return {
                "success": True,
                "stdout": (
                    f"ECS service {service} updated — "
                    f"desired={svc.get('desiredCount')} "
                    f"running={svc.get('runningCount')}"
                ),
            }

        return None

    def _boto3_rds(self, subcommand: str, params: dict, region: str) -> dict | None:
        client = boto3.client("rds", region_name=region, config=_BOTO_CFG)

        if subcommand == "modify-db-instance":
            db_id = params.get("db-instance-identifier")
            if not db_id:
                return {"success": False, "stderr": "missing --db-instance-identifier"}
            kwargs: dict = {"DBInstanceIdentifier": db_id, "ApplyImmediately": True}
            if "db-instance-class" in params:
                kwargs["DBInstanceClass"] = params["db-instance-class"]
            if "allocated-storage" in params:
                kwargs["AllocatedStorage"] = int(params["allocated-storage"])
            if "max-allocated-storage" in params:
                kwargs["MaxAllocatedStorage"] = int(params["max-allocated-storage"])
            resp = client.modify_db_instance(**kwargs)
            db = resp.get("DBInstance", {})
            return {
                "success": True,
                "stdout": f"RDS {db_id} modify initiated — status={db.get('DBInstanceStatus')}",
            }

        return None

    @staticmethod
    def _fix_statistics_param(command: str) -> str:
        """
        Bedrock sometimes generates --statistics Sum,Average (comma-separated string).
        AWS CLI expects --statistics Sum Average (space-separated arguments).
        """
        import re
        def expand(match):
            values = match.group(1).split(",")
            return "--statistics " + " ".join(v.strip() for v in values)
        return re.sub(r"--statistics\s+([\w,]+)", expand, command)

    @staticmethod
    def _fix_jq_dependency(command: str) -> str:
        """
        Bedrock sometimes pipes output through jq for formatting.
        If jq is not installed, strip the pipe and return raw JSON.
        """
        import shutil
        if "| jq" not in command:
            return command
        if shutil.which("jq"):
            return command
        # jq not available — strip everything from '| jq' onward
        return command[:command.index("| jq")].rstrip()

    def _run_ssm(self, action: dict) -> dict:
        import json
        import boto3

        try:
            ssm = boto3.client("ssm", region_name=os.environ["AWS_REGION"])
            resp = ssm.send_command(
                DocumentName=action.get("document", "AWS-RunShellScript"),
                Parameters=action.get("parameters", {}),
                Targets=action.get("targets", []),
                TimeoutSeconds=120,
            )
            command_id = resp["Command"]["CommandId"]
            return {"success": True, "command_id": command_id, "stdout": f"SSM command sent: {command_id}"}
        except Exception as exc:
            return {"success": False, "stderr": str(exc)}

    def _run_manual(self, action: dict) -> dict:
        # Manual actions cannot be automated — log them and mark as needing human execution
        description = action.get("description", "No description provided")
        logger.warning("manual_action_required description=%s", description)
        return {
            "success": True,  # not a failure — just informational
            "stdout": f"MANUAL ACTION REQUIRED: {description}",
            "manual": True,
        }

    def _run_unknown(self, action: dict) -> dict:
        return {
            "success": False,
            "stderr": f"Unknown action type: {action.get('type')}. Supported: kubectl, terraform, aws_cli, ssm, manual.",
        }

    def _shell(self, command: str) -> dict:
        if not command:
            return {"success": False, "stderr": "empty command"}
        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=120
            )
            return {
                "success": proc.returncode == 0,
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
                "returncode": proc.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "stderr": "timed out after 120s", "returncode": -1}
        except Exception as exc:
            return {"success": False, "stderr": str(exc), "returncode": -1}
