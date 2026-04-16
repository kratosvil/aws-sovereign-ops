import logging
import os
from datetime import datetime, timedelta, timezone

import boto3

logger = logging.getLogger(__name__)

SCHEMA = {
    "name": "validate_fix",
    "description": (
        "Checks post-fix metrics to confirm the incident is resolved. "
        "Works for any AWS resource type: eks_pod, ecs_service, rds, lambda, alb, generic. "
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
                    "description": "Name of the resource that was fixed.",
                },
                "resource_type": {
                    "type": "string",
                    "enum": ["eks_pod", "ecs_service", "rds", "lambda", "alb", "generic"],
                    "description": "Same resource_type used in analyze_incident.",
                    "default": "generic",
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

# Key metric per resource type used to determine if the fix worked.
# Format: (metric_id, namespace, metric_name, dimension_name, stat, failure_threshold)
# fix_confirmed = True when the metric is BELOW the threshold (or has no data = no new events)
HEALTH_CHECK_METRIC = {
    "eks_pod": ("restarts", "ContainerInsights", "pod_number_of_container_restarts", "PodName", "Sum", 1),
    "ecs_service": ("cpu_util", "AWS/ECS", "CPUUtilization", "ServiceName", "Average", 85),
    "rds": ("cpu_util", "AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", "Average", 85),
    "lambda": ("errors", "AWS/Lambda", "Errors", "FunctionName", "Sum", 1),
    "alb": ("5xx", "AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", "Sum", 5),
    "generic": None,
}


class ValidateFixTool:
    def __init__(self):
        region = os.environ["AWS_REGION"]
        self.cw = boto3.client("cloudwatch", region_name=region)
        self.project = os.environ.get("PROJECT_NAME", "sovereign-aiops")

    def execute(
        self,
        incident_id: str,
        resource_name: str,
        resource_type: str = "generic",
        lookback_minutes: int = 10,
    ) -> dict:
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=lookback_minutes)

        metrics = self._get_health_metric(resource_name, resource_type, start, end)
        active_alarms = self._get_active_alarms()

        fix_confirmed, reason = self._assess(resource_type, metrics, active_alarms)

        logger.info(
            "validate_fix incident_id=%s resource=%s type=%s fix_confirmed=%s reason=%s",
            incident_id, resource_name, resource_type, fix_confirmed, reason,
        )

        return {
            "incident_id": incident_id,
            "resource_name": resource_name,
            "resource_type": resource_type,
            "fix_confirmed": fix_confirmed,
            "metrics_post_fix": metrics,
            "active_alarms_count": len(active_alarms),
            "active_alarms": active_alarms,
            "assessment": reason,
        }

    def _get_health_metric(
        self, resource_name: str, resource_type: str, start: datetime, end: datetime
    ) -> dict:
        spec = HEALTH_CHECK_METRIC.get(resource_type)
        if not spec:
            return {}

        metric_id, namespace, metric_name, dim_name, stat, _ = spec
        try:
            resp = self.cw.get_metric_data(
                MetricDataQueries=[
                    {
                        "Id": metric_id,
                        "MetricStat": {
                            "Metric": {
                                "Namespace": namespace,
                                "MetricName": metric_name,
                                "Dimensions": [{"Name": dim_name, "Value": resource_name}],
                            },
                            "Period": 60,
                            "Stat": stat,
                        },
                    }
                ],
                StartTime=start,
                EndTime=end,
            )
            result = resp["MetricDataResults"][0]
            return {
                metric_id: {
                    "values": result.get("Values", []),
                    "timestamps": [t.isoformat() for t in result.get("Timestamps", [])],
                }
            }
        except Exception as exc:
            logger.warning("health_metric_failed resource=%s error=%s", resource_name, exc)
            return {"error": str(exc)}

    def _get_active_alarms(self) -> list:
        try:
            resp = self.cw.describe_alarms(
                AlarmNamePrefix=self.project,
                StateValue="ALARM",
            )
            return [
                {"name": a["AlarmName"], "reason": a["StateReason"]}
                for a in resp.get("MetricAlarms", [])
            ]
        except Exception as exc:
            logger.warning("active_alarms_check_failed error=%s", exc)
            return []

    def _assess(self, resource_type: str, metrics: dict, active_alarms: list) -> tuple:
        spec = HEALTH_CHECK_METRIC.get(resource_type)

        # If alarms are still firing for this project — fix did not work
        if active_alarms:
            return False, f"Fix did NOT resolve the issue — {len(active_alarms)} alarm(s) still active."

        if not spec or "error" in metrics:
            return True, "No active alarms detected. Assuming fix succeeded (no health metric available)."

        metric_id, _, _, _, _, threshold = spec
        values = metrics.get(metric_id, {}).get("values", [])

        if not values:
            return True, "No new metric events detected post-fix. Fix appears successful."

        peak = max(values)
        if peak >= threshold:
            return False, f"Peak value {peak} still exceeds threshold {threshold}. Fix did NOT resolve the issue."

        return True, f"Peak value {peak} is below threshold {threshold}. Fix confirmed successful."
