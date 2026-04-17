variable "project_name" {
  description = "Resource name prefix applied to all resources in this module."
  type        = string
}

variable "alb_arn_suffix" {
  description = "ALB ARN suffix for ALB metrics. From module.ecs_fargate outputs or aws_lb data source. Format: app/name/id."
  type        = string
  default     = ""
}

variable "enable_alb_alarms" {
  description = "Set to true to create ALB high-latency and 5XX error rate alarms. Requires alb_arn_suffix."
  type        = bool
  default     = false
}

variable "enable_eks_alarms" {
  description = "Set to true to create EKS OOMKilled alarm. Requires eks_cluster_name."
  type        = bool
  default     = false
}

variable "enable_ecs_alarms" {
  description = "Set to true to create ECS CPU utilization alarm. Requires ecs_cluster_name and ecs_service_name."
  type        = bool
  default     = false
}

variable "eks_cluster_name" {
  description = "EKS cluster name for OOMKilled and node metrics. Leave empty if not using EKS."
  type        = string
  default     = ""
}

variable "ecs_cluster_name" {
  description = "ECS cluster name for CPU/memory metrics on the MCP Server."
  type        = string
  default     = ""
}

variable "ecs_service_name" {
  description = "ECS service name for CPU/memory metrics on the MCP Server."
  type        = string
  default     = ""
}

variable "latency_threshold_seconds" {
  description = "ALB p99 latency threshold in seconds. Alarm triggers when exceeded for eval_periods."
  type        = number
  default     = 2
}

variable "error_rate_threshold_percent" {
  description = "ALB 5XX error rate threshold as a percentage (0-100)."
  type        = number
  default     = 5
}

variable "cpu_threshold_percent" {
  description = "ECS CPU utilization threshold percentage. Alarm triggers when exceeded."
  type        = number
  default     = 85
}

variable "eval_periods" {
  description = "Number of consecutive periods the metric must breach the threshold before alarming."
  type        = number
  default     = 2
}

variable "period_seconds" {
  description = "Metric evaluation period in seconds."
  type        = number
  default     = 300
}

variable "alarm_email" {
  description = "Email address to notify on alarm state changes via SNS. Leave empty to skip email subscription."
  type        = string
  default     = ""
}

variable "mcp_server_lambda_arn" {
  description = "Lambda or ECS task ARN to trigger when an alarm fires. Used by EventBridge rule target."
  type        = string
}

variable "sns_topic_arn" {
  description = "ARN of the SNS topic for alarm notifications. Create the topic outside this module (e.g. as a standalone resource in main.tf) and pass its ARN here. This avoids circular dependencies with other modules that also need the SNS topic ARN."
  type        = string
}

variable "tags" {
  description = "Additional tags merged into all resources."
  type        = map(string)
  default     = {}
}
