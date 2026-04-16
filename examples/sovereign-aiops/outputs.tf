output "vpc_id" {
  description = "VPC ID of the zero-egress network."
  value       = module.networking.vpc_id
}

output "ecr_repository_url" {
  description = "ECR repository URL. Push the MCP Server image here before running the demo."
  value       = module.ecr.repository_url
}

output "mcp_server_service_arn" {
  description = "ECS Service ARN for the MCP Server."
  value       = module.ecs_fargate.service_arn
}

output "cloudtrail_bucket" {
  description = "S3 bucket where CloudTrail WORM logs are stored."
  value       = module.cloudtrail_audit.s3_bucket_id
}

output "cloudtrail_kms_key_arn" {
  description = "KMS key ARN used to encrypt CloudTrail logs."
  value       = module.cloudtrail_audit.kms_key_arn
}

output "sns_topic_arn" {
  description = "SNS Topic ARN for alarm notifications. Operator receives APPROVE/REJECT via this topic."
  value       = module.cloudwatch_alarms.sns_topic_arn
}

output "bedrock_endpoint_sg_id" {
  description = "Security Group ID attached to the Bedrock/CW/STS VPC Interface Endpoints."
  value       = module.bedrock_privatelink.endpoint_sg_id
}

output "hitl_approve_url" {
  description = "Public URL template for operator approval. Replace INCIDENT_ID and TOKEN."
  value       = module.hitl_notifier.approve_url_template
}

output "hitl_api_base_url" {
  description = "API Gateway base URL for HITL approve/reject endpoints."
  value       = module.hitl_notifier.api_base_url
}
