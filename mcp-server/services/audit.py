import json
import logging
import os
from datetime import datetime, timezone

# Logs go to stdout → CloudWatch Logs → CloudTrail picks up the API calls.
# The audit logger writes structured JSON so each entry is searchable.
logger = logging.getLogger("sovereign-aiops.audit")


class AuditService:
    def __init__(self):
        self.project = os.environ.get("PROJECT_NAME", "sovereign-aiops")

    def log(self, event_type: str, incident_id: str, data: dict) -> None:
        entry = {
            "audit": True,
            "event_type": event_type,
            "incident_id": incident_id,
            "project": self.project,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **data,
        }
        logger.info(json.dumps(entry))

    # Convenience wrappers — one per meaningful action in the flow

    def incident_received(self, incident_id: str, alarm_name: str) -> None:
        self.log("incident_received", incident_id, {"alarm_name": alarm_name})

    def analysis_complete(self, incident_id: str, resource: str) -> None:
        self.log("analysis_complete", incident_id, {"resource": resource})

    def fix_proposed(self, incident_id: str, risk: str, fix_description: str) -> None:
        self.log("fix_proposed", incident_id, {"risk": risk, "fix": fix_description})

    def hitl_notified(self, incident_id: str) -> None:
        self.log("hitl_notified", incident_id, {})

    def approval_received(self, incident_id: str, approved_by: str) -> None:
        self.log("approval_received", incident_id, {"approved_by": approved_by})

    def approval_rejected(self, incident_id: str, rejected_by: str) -> None:
        self.log("approval_rejected", incident_id, {"rejected_by": rejected_by})

    def execution_complete(self, incident_id: str, actions: list, success: bool) -> None:
        self.log("execution_complete", incident_id, {"actions": actions, "success": success})

    def validation_complete(self, incident_id: str, fix_confirmed: bool) -> None:
        self.log("validation_complete", incident_id, {"fix_confirmed": fix_confirmed})

    def rollback_executed(self, incident_id: str, approved_by: str) -> None:
        self.log("rollback_executed", incident_id, {"approved_by": approved_by})

    def circuit_breaker_triggered(self, incident_id: str, consecutive_failures: int) -> None:
        self.log(
            "circuit_breaker_triggered",
            incident_id,
            {"consecutive_failures": consecutive_failures},
        )
