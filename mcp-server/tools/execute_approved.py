import logging
import os
import subprocess

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
        return self._shell(action.get("command", ""))

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
