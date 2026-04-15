import logging
import subprocess

logger = logging.getLogger(__name__)

SCHEMA = {
    "name": "rollback",
    "description": (
        "Reverts the last fix if validate_fix confirms the issue persists or worsened. "
        "Requires a valid HITL approval token — a failed fix still needs human sign-off to revert. "
        "Runs 'terraform destroy' on the applied diff and reverts kubectl changes."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "Incident ID to roll back.",
                },
                "token": {
                    "type": "string",
                    "description": "HITL approval token (same token used in execute_approved).",
                },
                "kubectl_undo_commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Kubectl commands to revert the changes (e.g. rollout undo).",
                    "default": [],
                },
                "terraform_destroy": {
                    "type": "boolean",
                    "description": "If true, runs terraform destroy on the applied diff.",
                    "default": False,
                },
            },
            "required": ["incident_id", "token"],
        }
    },
}


class RollbackTool:
    def __init__(self, hitl_service):
        self.hitl = hitl_service

    def execute(
        self,
        incident_id: str,
        token: str,
        kubectl_undo_commands: list = None,
        terraform_destroy: bool = False,
    ) -> dict:
        if not self.hitl.validate_token(token, incident_id):
            logger.warning("rollback_rejected invalid_token incident_id=%s", incident_id)
            return {
                "status": "rejected",
                "reason": "Invalid or expired approval token.",
            }

        actions = []
        errors = []

        for cmd in (kubectl_undo_commands or []):
            result = self._run(cmd)
            actions.append({"command": cmd, **result})
            if not result["success"]:
                errors.append(cmd)
                logger.error("rollback_kubectl_failed cmd=%s stderr=%s", cmd, result.get("stderr"))

        if terraform_destroy:
            tf_result = self._terraform_destroy()
            actions.append({"command": "terraform destroy", **tf_result})
            if not tf_result["success"]:
                errors.append("terraform destroy")

        success = len(errors) == 0
        logger.info(
            "rollback_complete incident_id=%s success=%s actions=%d",
            incident_id, success, len(actions),
        )

        return {
            "status": "rolled_back" if success else "rollback_partial_failure",
            "incident_id": incident_id,
            "actions_taken": actions,
            "errors": errors,
            "success": success,
            "next_step": "Incident escalated to engineering team for manual review." if not success else "Rollback complete. Monitor metrics.",
        }

    def _run(self, command: str) -> dict:
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

    def _terraform_destroy(self) -> dict:
        import os
        tf_dir = os.environ.get("TERRAFORM_WORKING_DIR", "/tmp/tf-apply")
        return self._run(
            f"terraform -chdir={tf_dir} destroy -auto-approve -input=false"
        )
