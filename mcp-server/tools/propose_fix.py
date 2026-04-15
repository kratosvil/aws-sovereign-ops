import logging
from typing import Optional

logger = logging.getLogger(__name__)

SCHEMA = {
    "name": "propose_fix",
    "description": (
        "Records the diagnosis and fix you have determined from the incident analysis. "
        "Call this after analyze_incident once you have identified the root cause. "
        "This does NOT execute anything — it formalizes your proposal for human review."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "root_cause": {
                    "type": "string",
                    "description": "Clear explanation of why the failure occurred.",
                },
                "fix_description": {
                    "type": "string",
                    "description": "Plain-language description of the fix to apply.",
                },
                "risk": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": (
                        "Risk level of the fix. "
                        "low=config change, medium=rolling restart, high=destructive or irreversible."
                    ),
                },
                "expected_outcome": {
                    "type": "string",
                    "description": "What the system state should look like after the fix succeeds.",
                },
                "kubectl_commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Kubectl commands to execute (in order). Empty if not applicable.",
                    "default": [],
                },
                "terraform_diff": {
                    "type": "string",
                    "description": "Terraform HCL diff if infrastructure changes are needed. Null if not applicable.",
                },
            },
            "required": ["root_cause", "fix_description", "risk", "expected_outcome"],
        }
    },
}

# Module-level store — the orchestrator reads this after Bedrock calls propose_fix
_current_proposal: Optional[dict] = None


class ProposeFixTool:
    def execute(
        self,
        root_cause: str,
        fix_description: str,
        risk: str,
        expected_outcome: str,
        kubectl_commands: list = None,
        terraform_diff: str = None,
    ) -> dict:
        global _current_proposal

        proposal = {
            "root_cause": root_cause,
            "fix_description": fix_description,
            "risk": risk,
            "expected_outcome": expected_outcome,
            "kubectl_commands": kubectl_commands or [],
            "terraform_diff": terraform_diff,
        }

        _current_proposal = proposal

        logger.info(
            "fix_proposed risk=%s fix=%s",
            risk,
            fix_description[:80],
        )

        return {
            "status": "proposal_recorded",
            "message": "Fix proposal recorded. Awaiting human approval before execution.",
            "proposal": proposal,
        }


def get_current_proposal() -> Optional[dict]:
    """Called by the orchestrator to retrieve the proposal after Bedrock records it."""
    return _current_proposal


def clear_proposal() -> None:
    """Reset between incidents."""
    global _current_proposal
    _current_proposal = None
