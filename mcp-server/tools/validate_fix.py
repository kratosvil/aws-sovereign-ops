import logging
import os
from datetime import datetime, timedelta, timezone

import boto3

logger = logging.getLogger(__name__)

SCHEMA = {
    "name": "validate_fix",
    "description": (
        "Checks post-fix metrics to confirm the incident is resolved. "
        "Pulls CloudWatch metrics for the resource and returns current state. "
        "Call this after execute_approved. "
        "If metrics show the issue persists, call rollback next."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "Incident ID being validated.",
                },
                "resource_name": {
                    "type": "string",
                    "description": "Name of the pod or ECS task that was fixed.",
                },
                "lookback_minutes": {
                    "type": "integer",
                    "description": "How many minutes of post-fix data to check.",
                    "default": 10,
                },
            },
            "required": ["incident_id", "resource_name"],
        }
    },
}


class ValidateFixTool:
    def __init__(self):
        region = os.environ["AWS_REGION"]
        self.cw = boto3.client("cloudwatch", region_name=region)
        self.project = os.environ.get("PROJECT_NAME", "sovereign-aiops")

    def execute(
        self, incident_id: str, resource_name: str, lookback_minutes: int = 10
    ) -> dict:
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=lookback_minutes)

        metrics = self._get_metrics(resource_name, start, end)
        alarm_state = self._get_alarm_state(resource_name)

        # Determine if fix succeeded based on metrics
        mem_values = metrics.get("mem", {}).get("values", [])
        mem_max = max(mem_values) if mem_values else None
        alarms_in_alert = [a for a in alarm_state if a["state"] == "ALARM"]

        fix_confirmed = len(alarms_in_alert) == 0 and (mem_max is None or mem_max < 90)

        logger.info(
            "validate_fix incident_id=%s resource=%s fix_confirmed=%s mem_max=%s alarms=%d",
            incident_id, resource_name, fix_confirmed, mem_max, len(alarms_in_alert),
        )

        return {
            "incident_id": incident_id,
            "resource_name": resource_name,
            "fix_confirmed": fix_confirmed,
            "metrics_post_fix": metrics,
            "active_alarms": alarms_in_alert,
            "memory_max_percent": mem_max,
            "assessment": (
                "Fix confirmed — resource is stable."
                if fix_confirmed
                else "Fix did NOT resolve the issue — consider rollback."
            ),
        }

    def _get_metrics(self, resource_name: str, start: datetime, end: datetime) -> dict:
        try:
            resp = self.cw.get_metric_data(
                MetricDataQueries=[
                    {
                        "Id": "mem",
                        "MetricStat": {
                            "Metric": {
                                "Namespace": "ContainerInsights",
                                "MetricName": "pod_memory_utilization",
                                "Dimensions": [{"Name": "PodName", "Value": resource_name}],
                            },
                            "Period": 60,
                            "Stat": "Maximum",
                        },
                    },
                    {
                        "Id": "restarts",
                        "MetricStat": {
                            "Metric": {
                                "Namespace": "ContainerInsights",
                                "MetricName": "pod_number_of_container_restarts",
                                "Dimensions": [{"Name": "PodName", "Value": resource_name}],
                            },
                            "Period": 60,
                            "Stat": "Sum",
                        },
                    },
                ],
                StartTime=start,
                EndTime=end,
            )
            return {
                r["Id"]: {
                    "values": r.get("Values", []),
                    "timestamps": [t.isoformat() for t in r.get("Timestamps", [])],
                }
                for r in resp.get("MetricDataResults", [])
            }
        except Exception as exc:
            logger.warning("post_fix_metrics_failed resource=%s error=%s", resource_name, exc)
            return {"error": str(exc)}

    def _get_alarm_state(self, resource_name: str) -> list:
        try:
            resp = self.cw.describe_alarms(
                AlarmNamePrefix=self.project,
                StateValue="ALARM",
            )
            return [
                {"name": a["AlarmName"], "state": a["StateValue"], "reason": a["StateReason"]}
                for a in resp.get("MetricAlarms", [])
            ]
        except Exception as exc:
            logger.warning("alarm_state_check_failed error=%s", exc)
            return []
