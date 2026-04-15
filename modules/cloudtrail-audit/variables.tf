variable "project_name" {
  description = "Resource name prefix applied to all resources in this module."
  type        = string
}

variable "retention_days" {
  description = "Object Lock retention period in days. Logs cannot be deleted or modified during this period."
  type        = number
  default     = 90
}

variable "enable_cloudwatch_logs" {
  description = "Stream CloudTrail events to a CloudWatch Log Group for real-time alerting."
  type        = bool
  default     = true
}

variable "cloudwatch_log_retention_days" {
  description = "CloudWatch Log Group retention in days. Independent from S3 WORM retention."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Additional tags merged into all resources."
  type        = map(string)
  default     = {}
}
