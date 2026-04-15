variable "project_name" {
  description = "Resource name prefix applied to all resources in this module."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID where the endpoints will be created. From module.networking.vpc_id."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnet IDs where endpoint ENIs will be placed. From module.networking.subnet_private_ids."
  type        = list(string)
}

variable "allowed_sg_ids" {
  description = "Security group IDs allowed to reach the endpoints (typically the MCP Server SG). From module.networking.sg_app_id."
  type        = list(string)
}

variable "enable_bedrock_agent" {
  description = "Create endpoint for bedrock-agent service (required if using Bedrock Agents, not needed for direct InvokeModel)."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Additional tags merged into all resources."
  type        = map(string)
  default     = {}
}
