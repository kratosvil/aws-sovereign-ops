import logging
import os
import time
from datetime import datetime, timedelta, timezone

import boto3

logger = logging.getLogger(__name__)

SCHEMA = {
    "name": "analyze_incident",
    "description": (
        "Pulls CloudWatch logs and metrics for any AWS resource that triggered an alarm. "
        "Works for any alarm type: OOMKilled, high latency, 5XX errors, CPU overload, "
        "RDS connections, Lambda timeouts, DynamoDB throttles, etc. "
        "Pass the alarm_name and resource_name from the event. "
        "Always call this before propose_fix."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "resource_name": {
                    "type": "string",
                    "description": (
                        "Identifier of the affected resource. "
                        "Examples: pod name, ECS service name, RDS instance ID, "
                        "Lambda function name, ALB name."
                    ),
                },
                "resource_type": {
                    "type": "string",
                    "enum": ["eks_pod", "ecs_service", "rds", "lambda", "alb", "generic"],
                    "description": (
                        "Type of AWS resource. Controls which metrics and log groups are queried. "
                        "Use 'generic' if unsure — it will query CloudWatch Logs with the resource name."
                    ),
                    "default": "generic",
                },
                "alarm_name": {
                    "type": "string",
                    "description": "CloudWatch alarm name from the triggering event.",
                },
                "lookback_minutes": {
                    "type": "integer",
                    "description": "How many minutes back to pull data.",
                    "default": 60,
                },
            },
            "required": ["resource_name", "alarm_name"],
        }
    },
}

# Metric queries per resource type — extensible without touching orchestrator or Bedrock
METRIC_QUERIES = {
    "eks_pod": [
        ("mem_util", "ContainerInsights", "pod_memory_utilization", "PodName", "Maximum"),
        ("cpu_util", "ContainerInsights", "pod_cpu_utilization", "PodName", "Average"),
        ("restarts", "ContainerInsights", "pod_number_of_container_restarts", "PodName", "Sum"),
    ],
    "ecs_service": [
        ("cpu_util", "AWS/ECS", "CPUUtilization", "ServiceName", "Average"),
        ("mem_util", "AWS/ECS", "MemoryUtilization", "ServiceName", "Average"),
    ],
    "rds": [
        ("cpu_util", "AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", "Average"),
        ("connections", "AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", "Maximum"),
        ("read_latency", "AWS/RDS", "ReadLatency", "DBInstanceIdentifier", "Average"),
        ("write_latency", "AWS/RDS", "WriteLatency", "DBInstanceIdentifier", "Average"),
        ("freeable_mem", "AWS/RDS", "FreeableMemory", "DBInstanceIdentifier", "Minimum"),
    ],
    "lambda": [
        ("errors", "AWS/Lambda", "Errors", "FunctionName", "Sum"),
        ("duration", "AWS/Lambda", "Duration", "FunctionName", "Maximum"),
        ("throttles", "AWS/Lambda", "Throttles", "FunctionName", "Sum"),
        ("concurrent", "AWS/Lambda", "ConcurrentExecutions", "FunctionName", "Maximum"),
    ],
    "alb": [
        ("http5xx", "AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", "Sum"),
        ("latency_p99", "AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", "p99"),
        ("request_count", "AWS/ApplicationELB", "RequestCount", "LoadBalancer", "Sum"),
        ("healthy_hosts", "AWS/ApplicationELB", "HealthyHostCount", "LoadBalancer", "Minimum"),
    ],
    "generic": [],  # falls back to log search only
}

LOG_GROUPS = {
    "eks_pod": [
        "/aws/eks/{project}/containers",
        "/aws/containerinsights/{project}/performance",
    ],
    "ecs_service": [
        "/ecs/{project}",
        "/aws/ecs/containerinsights/{project}/performance",
    ],
    "rds": [
        "/aws/rds/instance/{resource}/error",
        "/aws/rds/instance/{resource}/general",
    ],
    "lambda": [
        "/aws/lambda/{resource}",
    ],
    "alb": [
        "/aws/applicationelb/{project}",
    ],
    "generic": [
        "/aws/{resource}",
        "/aws/cloudwatch/{project}",
    ],
}


class AnalyzeIncidentTool:
    def __init__(self):
        region = os.environ["AWS_REGION"]
        self.cw_logs = boto3.client("logs", region_name=region)
        self.cw = boto3.client("cloudwatch", region_name=region)
        self.project = os.environ.get("PROJECT_NAME", "sovereign-aiops")

    def execute(
        self,
        resource_name: str,
        alarm_name: str,
        resource_type: str = "generic",
        lookback_minutes: int = 60,
    ) -> dict:
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=lookback_minutes)

        metrics = self._get_metrics(resource_name, resource_type, start, end)
        logs = self._get_logs(resource_name, resource_type, start, end)
        recent_events = self._get_recent_alarm_events(alarm_name, start, end)

        logger.info(
            "analyze_incident resource=%s type=%s alarm=%s logs=%d metrics_keys=%s",
            resource_name,
            resource_type,
            alarm_name,
            len(logs),
            list(metrics.keys()),
        )

        return {
            "resource_name": resource_name,
            "resource_type": resource_type,
            "alarm_name": alarm_name,
            "analysis_window_minutes": lookback_minutes,
            "logs_sample": logs[:50],
            "metrics": metrics,
            "recent_alarm_events": recent_events,
            "collected_at": end.isoformat(),
        }

    def _get_metrics(
        self, resource_name: str, resource_type: str, start: datetime, end: datetime
    ) -> dict:
        queries_spec = METRIC_QUERIES.get(resource_type, [])
        if not queries_spec:
            return {}

        queries = []
        for metric_id, namespace, metric_name, dim_name, stat in queries_spec:
            queries.append(
                {
                    "Id": metric_id,
                    "MetricStat": {
                        "Metric": {
                            "Namespace": namespace,
                            "MetricName": metric_name,
                            "Dimensions": [{"Name": dim_name, "Value": resource_name}],
                        },
                        "Period": 300,
                        "Stat": stat,
                    },
                }
            )

        try:
            resp = self.cw.get_metric_data(
                MetricDataQueries=queries, StartTime=start, EndTime=end
            )
            return {
                r["Id"]: {
                    "values": r.get("Values", []),
                    "timestamps": [t.isoformat() for t in r.get("Timestamps", [])],
                    "label": r.get("Label", r["Id"]),
                }
                for r in resp.get("MetricDataResults", [])
            }
        except Exception as exc:
            logger.warning("metric_retrieval_failed resource=%s error=%s", resource_name, exc)
            return {"error": str(exc)}

    def _get_logs(
        self, resource_name: str, resource_type: str, start: datetime, end: datetime
    ) -> list:
        log_group_templates = LOG_GROUPS.get(resource_type, LOG_GROUPS["generic"])
        logs = []

        for template in log_group_templates:
            log_group = template.format(project=self.project, resource=resource_name)
            try:
                resp = self.cw_logs.filter_log_events(
                    logGroupName=log_group,
                    filterPattern=resource_name,
                    startTime=int(start.timestamp() * 1000),
                    endTime=int(end.timestamp() * 1000),
                    limit=30,
                )
                logs.extend(e["message"] for e in resp.get("events", []))
                if logs:
                    break  # stop at first log group that returns data
            except self.cw_logs.exceptions.ResourceNotFoundException:
                continue
            except Exception as exc:
                logger.warning("log_retrieval_failed group=%s error=%s", log_group, exc)

        return logs

    def _get_recent_alarm_events(self, alarm_name: str, start: datetime, end: datetime) -> list:
        """Returns recent state transitions for this alarm from CloudWatch."""
        try:
            resp = self.cw.describe_alarm_history(
                AlarmName=alarm_name,
                HistoryItemType="StateUpdate",
                StartDate=start,
                EndDate=end,
                MaxRecords=10,
            )
            return [
                {
                    "timestamp": h["Timestamp"].isoformat(),
                    "summary": h["HistorySummary"],
                }
                for h in resp.get("AlarmHistoryItems", [])
            ]
        except Exception as exc:
            logger.warning("alarm_history_failed alarm=%s error=%s", alarm_name, exc)
            return []
