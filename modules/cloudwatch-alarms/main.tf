# ==============================================================
# cloudwatch-alarms — Eyes of the sovereign-aiops agent
# Layer: ORGANIZATION
# ==============================================================

locals {
  tags = merge(
    {
      Project   = var.project_name
      Module    = "cloudwatch-alarms"
      ManagedBy = "terraform"
    },
    var.tags
  )

  enable_alb_alarms = var.enable_alb_alarms
  enable_eks_alarms = var.enable_eks_alarms
  enable_ecs_alarms = var.enable_ecs_alarms
}

# --------------------------------------------------------------
# SNS SUBSCRIPTION — email alert when alarm fires
# SNS topic is created outside this module (see examples/main.tf)
# to avoid circular dependencies.
# --------------------------------------------------------------
resource "aws_sns_topic_subscription" "email" {
  count = var.alarm_email != "" ? 1 : 0

  topic_arn = var.sns_topic_arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# --------------------------------------------------------------
# ALARM — OOMKilled pod in EKS
# Metric published by kube-state-metrics (container_oom_killer_total)
# --------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "oomkilled" {
  count = local.enable_eks_alarms ? 1 : 0

  alarm_name          = "${var.project_name}-oomkilled"
  alarm_description   = "One or more EKS pods terminated due to OOMKilled. Agent will analyze and propose fix."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "container_oom_killer_total"
  namespace           = "ContainerInsights"
  period              = 60
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "notBreaching"

  dimensions = {
    ClusterName = var.eks_cluster_name
  }

  alarm_actions = [var.sns_topic_arn]
  ok_actions    = [var.sns_topic_arn]

  tags = merge(local.tags, { AlarmType = "oomkilled" })
}

# --------------------------------------------------------------
# ALARM — ALB p99 latency above threshold
# --------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "high_latency" {
  count = local.enable_alb_alarms ? 1 : 0

  alarm_name          = "${var.project_name}-high-latency"
  alarm_description   = "ALB p99 latency exceeded ${var.latency_threshold_seconds}s. Possible memory pressure or connection exhaustion."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = var.eval_periods
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = var.period_seconds
  extended_statistic  = "p99"
  threshold           = var.latency_threshold_seconds
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
  }

  alarm_actions = [var.sns_topic_arn]
  ok_actions    = [var.sns_topic_arn]

  tags = merge(local.tags, { AlarmType = "latency" })
}

# --------------------------------------------------------------
# ALARM — ALB 5XX error rate above threshold
# --------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "error_rate_5xx" {
  count = local.enable_alb_alarms ? 1 : 0

  alarm_name          = "${var.project_name}-error-rate-5xx"
  alarm_description   = "ALB 5XX error rate exceeded ${var.error_rate_threshold_percent}%. Service is returning errors."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = var.eval_periods
  threshold           = var.error_rate_threshold_percent
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "error_rate"
    expression  = "errors / MAX([errors, requests]) * 100"
    label       = "5XX Error Rate %"
    return_data = true
  }

  metric_query {
    id = "errors"
    metric {
      metric_name = "HTTPCode_Target_5XX_Count"
      namespace   = "AWS/ApplicationELB"
      period      = var.period_seconds
      stat        = "Sum"
      dimensions = {
        LoadBalancer = var.alb_arn_suffix
      }
    }
  }

  metric_query {
    id = "requests"
    metric {
      metric_name = "RequestCount"
      namespace   = "AWS/ApplicationELB"
      period      = var.period_seconds
      stat        = "Sum"
      dimensions = {
        LoadBalancer = var.alb_arn_suffix
      }
    }
  }

  alarm_actions = [var.sns_topic_arn]
  ok_actions    = [var.sns_topic_arn]

  tags = merge(local.tags, { AlarmType = "error-rate" })
}

# --------------------------------------------------------------
# ALARM — ECS CPU overload (MCP Server itself)
# --------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "ecs_cpu" {
  count = local.enable_ecs_alarms ? 1 : 0

  alarm_name          = "${var.project_name}-ecs-cpu"
  alarm_description   = "MCP Server ECS CPU utilization exceeded ${var.cpu_threshold_percent}%. Agent may be overloaded."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = var.eval_periods
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = var.period_seconds
  statistic           = "Average"
  threshold           = var.cpu_threshold_percent
  treat_missing_data  = "notBreaching"

  dimensions = {
    ClusterName = var.ecs_cluster_name
    ServiceName = var.ecs_service_name
  }

  alarm_actions = [var.sns_topic_arn]
  ok_actions    = [var.sns_topic_arn]

  tags = merge(local.tags, { AlarmType = "ecs-cpu" })
}

# --------------------------------------------------------------
# EVENTBRIDGE RULE — routes alarm state changes to MCP Server
# --------------------------------------------------------------
resource "aws_cloudwatch_event_rule" "alarm_trigger" {
  name        = "${var.project_name}-alarm-trigger"
  description = "Routes CloudWatch Alarm state changes (OK→ALARM) to the MCP Server for autonomous remediation."

  event_pattern = jsonencode({
    source      = ["aws.cloudwatch"]
    detail-type = ["CloudWatch Alarm State Change"]
    detail = {
      state = {
        value = ["ALARM"]
      }
      alarmName = [{
        prefix = var.project_name
      }]
    }
  })

  tags = local.tags
}

resource "aws_cloudwatch_event_target" "mcp_server" {
  rule      = aws_cloudwatch_event_rule.alarm_trigger.name
  target_id = "mcp-server"
  arn       = var.mcp_server_lambda_arn

  input_transformer {
    input_paths = {
      alarm_name  = "$.detail.alarmName"
      alarm_state = "$.detail.state.value"
      reason      = "$.detail.state.reason"
      timestamp   = "$.time"
    }
    input_template = <<-EOT
      {
        "event_type": "alarm",
        "alarm_name": "<alarm_name>",
        "alarm_state": "<alarm_state>",
        "reason": "<reason>",
        "timestamp": "<timestamp>",
        "project": "${var.project_name}"
      }
    EOT
  }
}
