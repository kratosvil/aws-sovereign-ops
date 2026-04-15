# cloudwatch-alarms — AI Context

## What this module does

Creates CloudWatch Alarms that act as the eyes of the sovereign-aiops agent.
When infrastructure metrics breach defined thresholds, alarms fire to an SNS topic
and an EventBridge rule routes the event to the MCP Server for autonomous remediation.

## Alarms created

| Alarm | Trigger condition | Enabled when |
|-------|-------------------|--------------|
| oomkilled | EKS pod terminated by OOM (>= 1 event) | eks_cluster_name provided |
| high-latency | ALB p99 > latency_threshold_seconds | alb_arn_suffix provided |
| error-rate-5xx | ALB 5XX rate > error_rate_threshold_percent | alb_arn_suffix provided |
| ecs-cpu | MCP Server ECS CPU > cpu_threshold_percent | ecs_cluster_name + ecs_service_name provided |

## Conditional alarm creation

All alarms are conditional — they only create if the relevant input is provided.
This allows using the module in stages: start with OOMKilled only, add ALB alarms later.

## EventBridge routing

The EventBridge rule filters for:
- source: aws.cloudwatch
- detail-type: CloudWatch Alarm State Change
- state: ALARM only (not OK)
- alarmName prefix: project_name (only this project's alarms)

The event is transformed into a structured JSON payload before reaching the MCP Server:
- alarm_name, alarm_state, reason, timestamp, project

## Usage pattern

```hcl
module "cloudwatch_alarms" {
  source = "../../modules/cloudwatch-alarms"

  project_name          = var.project_name
  eks_cluster_name      = module.eks_cluster.cluster_name
  alb_arn_suffix        = module.ecs_fargate.alb_arn_suffix
  ecs_cluster_name      = module.ecs_fargate.cluster_name
  ecs_service_name      = module.ecs_fargate.service_name
  alarm_email           = var.operator_email
  mcp_server_lambda_arn = module.hitl_notifier.lambda_arn
}
```

## No VPC dependency

CloudWatch, SNS, and EventBridge are global services — no vpc_id needed.
