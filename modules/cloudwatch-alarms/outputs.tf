output "sns_topic_arn" {
  description = "SNS Topic ARN where all alarms publish."
  value       = var.sns_topic_arn
}

output "eventbridge_rule_arn" {
  description = "EventBridge Rule ARN that triggers the MCP Server on alarm state changes."
  value       = aws_cloudwatch_event_rule.alarm_trigger.arn
}

output "eventbridge_rule_name" {
  description = "EventBridge Rule name."
  value       = aws_cloudwatch_event_rule.alarm_trigger.name
}

output "alarm_oomkilled_arn" {
  description = "OOMKilled alarm ARN. Null if eks_cluster_name not provided."
  value       = local.enable_eks_alarms ? aws_cloudwatch_metric_alarm.oomkilled[0].arn : null
}

output "alarm_high_latency_arn" {
  description = "High latency alarm ARN. Null if alb_arn_suffix not provided."
  value       = local.enable_alb_alarms ? aws_cloudwatch_metric_alarm.high_latency[0].arn : null
}

output "alarm_error_rate_arn" {
  description = "5XX error rate alarm ARN. Null if alb_arn_suffix not provided."
  value       = local.enable_alb_alarms ? aws_cloudwatch_metric_alarm.error_rate_5xx[0].arn : null
}

output "alarm_ecs_cpu_arn" {
  description = "ECS CPU alarm ARN. Null if ecs_cluster_name/service_name not provided."
  value       = local.enable_ecs_alarms ? aws_cloudwatch_metric_alarm.ecs_cpu[0].arn : null
}
