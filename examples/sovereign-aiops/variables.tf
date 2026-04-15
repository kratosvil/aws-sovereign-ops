variable "project_name" {
  description = "Prefix applied to all resources in this stack."
  type        = string
  default     = "sovereign-aiops"
}

variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment label applied as a tag (demo, staging, prod)."
  type        = string
  default     = "demo"
}

variable "operator_email" {
  description = "Email address that receives APPROVE/REJECT notifications from the HITL Lambda."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets (one per AZ)."
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "cloudtrail_retention_days" {
  description = "Object Lock retention period in days. CloudTrail logs cannot be deleted during this period."
  type        = number
  default     = 90
}

variable "mcp_server_port" {
  description = "Port the MCP Server container listens on."
  type        = number
  default     = 8080
}

variable "mcp_server_cpu" {
  description = "ECS Fargate CPU units for the MCP Server task (256 = 0.25 vCPU)."
  type        = number
  default     = 256
}

variable "mcp_server_memory" {
  description = "ECS Fargate memory in MB for the MCP Server task."
  type        = number
  default     = 512
}
