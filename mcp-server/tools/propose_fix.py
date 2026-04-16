import logging
from typing import Optional

logger = logging.getLogger(__name__)

SCHEMA = {
    "name": "propose_fix",
    "description": (
        "Records the diagnosis and fix determined from the incident analysis. "
        "Call this after analyze_incident once the root cause is identified. "
        "Works for ANY AWS resource: EKS pods, ECS services, RDS, Lambda, ALB, "
        "DynamoDB, ElastiCache, S3, API Gateway, etc. "
        "This does NOT execute anything — it formalizes the proposal for human review."
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
                        "Risk level. "
                        "low=config/parameter change with no downtime. "
                        "medium=rolling restart or scaling operation. "
                        "high=destructive, irreversible, or affects multiple services."
                    ),
                },
                "expected_outcome": {
                    "type": "string",
                    "description": "What the system state should look like after the fix succeeds.",
                },
                "actions": {
                    "type": "array",
                    "description": (
                        "Ordered list of actions to execute. Each action has a 'type' field. "
                        "Supported types: "
                        "'kubectl' (command: str), "
                        "'terraform' (diff: str), "
                        "'aws_cli' (command: str — full aws CLI command), "
                        "'ssm' (document: str, parameters: dict, targets: list), "
                        "'manual' (description: str — cannot be automated, operator must do it)."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["kubectl", "terraform", "aws_cli", "ssm", "manual"],
                            },
                            "command": {"type": "string"},
                            "diff": {"type": "string"},
                            "document": {"type": "string"},
                            "parameters": {"type": "object"},
                            "targets": {"type": "array"},
                            "description": {"type": "string"},
                        },
                        "required": ["type"],
                    },
                    "default": [],
                },
            },
            "required": ["root_cause", "fix_description", "risk", "expected_outcome"],
        }
    },
}

_current_proposal: Optional[dict] = None


class ProposeFixTool:
    def execute(
        self,
        root_cause: str,
        fix_description: str,
        risk: str,
        expected_outcome: str,
        actions: list = None,
    ) -> dict:
        global _current_proposal

        proposal = {
            "root_cause": root_cause,
            "fix_description": fix_description,
            "risk": risk,
            "expected_outcome": expected_outcome,
            "actions": actions or [],
        }

        _current_proposal = proposal

        action_types = [a.get("type") for a in proposal["actions"]]
        logger.info("fix_proposed risk=%s actions=%s", risk, action_types)

        return {
            "status": "proposal_recorded",
            "message": "Fix proposal recorded. Awaiting human approval before execution.",
            "proposal": proposal,
        }


def get_current_proposal() -> Optional[dict]:
    return _current_proposal


def clear_proposal() -> None:
    global _current_proposal
    _current_proposal = None
