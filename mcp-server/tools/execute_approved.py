import logging
import os
import subprocess

logger = logging.getLogger(__name__)

SCHEMA = {
    "name": "execute_approved",
    "description": (
        "Executes the approved fix. Requires a valid HITL approval token. "
        "Runs kubectl commands and/or terraform apply from the proposal. "
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
                "kubectl_commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Kubectl commands to run (from propose_fix output).",
                    "default": [],
                },
                "terraform_diff": {
                    "type": "string",
                    "description": "Terraform diff to apply. Null if no infra changes.",
                },
            },
            "required": ["incident_id", "token"],
        }
    },
}


class ExecuteApprovedTool:
    def __init__(self, hitl_service):
        self.hitl = hitl_service

    def execute(
        self,
        incident_id: str,
        token: str,
        kubectl_commands: list = None,
        terraform_diff: str = None,
    ) -> dict:
        if not self.hitl.validate_token(token, incident_id):
            logger.warning("execute_rejected invalid_token incident_id=%s", incident_id)
            return {
                "status": "rejected",
                "reason": "Invalid or expired approval token. Request a new approval.",
            }

        actions_taken = []
        errors = []

        for cmd in (kubectl_commands or []):
            result = self._run(cmd)
            actions_taken.append({"command": cmd, **result})
            if not result["success"]:
                errors.append(cmd)
                logger.error("kubectl_failed cmd=%s stderr=%s", cmd, result.get("stderr"))

        if terraform_diff:
            tf_result = self._apply_terraform(terraform_diff)
            actions_taken.append({"command": "terraform apply", **tf_result})
            if not tf_result["success"]:
                errors.append("terraform apply")

        success = len(errors) == 0
        logger.info(
            "execution_complete incident_id=%s success=%s actions=%d errors=%d",
            incident_id, success, len(actions_taken), len(errors),
        )

        return {
            "status": "executed" if success else "partial_failure",
            "incident_id": incident_id,
            "actions_taken": actions_taken,
            "errors": errors,
            "success": success,
        }

    def _run(self, command: str) -> dict:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            return {
                "success": proc.returncode == 0,
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
                "returncode": proc.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "stderr": "command timed out after 120s", "returncode": -1}
        except Exception as exc:
            return {"success": False, "stderr": str(exc), "returncode": -1}

    def _apply_terraform(self, diff: str) -> dict:
        tf_dir = os.environ.get("TERRAFORM_WORKING_DIR", "/tmp/tf-apply")
        os.makedirs(tf_dir, exist_ok=True)

        plan_path = os.path.join(tf_dir, "fix.tf")
        with open(plan_path, "w") as f:
            f.write(diff)

        init = self._run(f"terraform -chdir={tf_dir} init -input=false")
        if not init["success"]:
            return init

        return self._run(
            f"terraform -chdir={tf_dir} apply -auto-approve -input=false"
        )
