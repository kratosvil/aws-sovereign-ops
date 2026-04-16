import logging
import os
import subprocess

logger = logging.getLogger(__name__)

SCHEMA = {
    "name": "rollback",
    "description": (
        "Reverts the last fix if validate_fix confirms the issue persists or worsened. "
        "Requires a valid HITL approval token. "
        "Accepts undo_actions — the reverse steps for each original action type. "
        "Works for any AWS resource: kubectl rollout undo, terraform destroy, "
        "aws_cli revert commands, SSM, or manual instructions."
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
                "undo_actions": {
                    "type": "array",
                    "description": (
                        "Reverse steps for each original action. Same structure as execute_approved actions. "
                        "Examples: "
                        "kubectl rollout undo, "
                        "terraform destroy, "
                        "aws_cli to revert a parameter change, "
                        "manual instructions if revert cannot be automated."
                    ),
                    "items": {"type": "object"},
                    "default": [],
                },
            },
            "required": ["incident_id", "token"],
        }
    },
}


class RollbackTool:
    def __init__(self, hitl_service):
        self.hitl = hitl_service

    def execute(self, incident_id: str, token: str, undo_actions: list = None) -> dict:
        if not self.hitl.validate_token(token, incident_id):
            logger.warning("rollback_rejected invalid_token incident_id=%s", incident_id)
            return {
                "status": "rejected",
                "reason": "Invalid or expired approval token.",
            }

        results = []
        errors = []

        for action in (undo_actions or []):
            action_type = action.get("type", "unknown")
            handler = getattr(self, f"_undo_{action_type}", self._undo_unknown)
            result = handler(action)
            results.append({"type": action_type, **result})

            if not result.get("success", False):
                errors.append(action_type)
                logger.error("rollback_action_failed type=%s result=%s", action_type, result)

        success = len(errors) == 0
        logger.info(
            "rollback_complete incident_id=%s success=%s actions=%d",
            incident_id, success, len(results),
        )

        return {
            "status": "rolled_back" if success else "rollback_partial_failure",
            "incident_id": incident_id,
            "results": results,
            "errors": errors,
            "success": success,
            "next_step": (
                "Rollback complete. Monitor metrics."
                if success
                else "Partial rollback — escalate to engineering team for manual review."
            ),
        }

    # ------------------------------------------------------------------
    # Undo handlers — mirror of execute_approved handlers
    # ------------------------------------------------------------------

    def _undo_kubectl(self, action: dict) -> dict:
        return self._shell(action.get("command", ""))

    def _undo_terraform(self, action: dict) -> dict:
        tf_dir = os.environ.get("TERRAFORM_WORKING_DIR", "/tmp/tf-apply")
        return self._shell(f"terraform -chdir={tf_dir} destroy -auto-approve -input=false")

    def _undo_aws_cli(self, action: dict) -> dict:
        return self._shell(action.get("command", ""))

    def _undo_ssm(self, action: dict) -> dict:
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
            return {"success": True, "stdout": f"SSM rollback command sent: {command_id}"}
        except Exception as exc:
            return {"success": False, "stderr": str(exc)}

    def _undo_manual(self, action: dict) -> dict:
        description = action.get("description", "No description provided")
        logger.warning("manual_rollback_required description=%s", description)
        return {
            "success": True,
            "stdout": f"MANUAL ROLLBACK REQUIRED: {description}",
            "manual": True,
        }

    def _undo_unknown(self, action: dict) -> dict:
        return {
            "success": False,
            "stderr": f"Unknown action type for rollback: {action.get('type')}",
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
