output "endpoint_sg_id" {
  description = "Security Group ID attached to all VPC Interface Endpoints. Pass to resources that need to call Bedrock or CloudWatch."
  value       = aws_security_group.endpoints.id
}

output "bedrock_runtime_endpoint_id" {
  description = "VPC Endpoint ID for Bedrock Runtime (InvokeModel)."
  value       = aws_vpc_endpoint.bedrock_runtime.id
}

output "bedrock_endpoint_id" {
  description = "VPC Endpoint ID for Bedrock control plane."
  value       = aws_vpc_endpoint.bedrock.id
}

output "bedrock_agent_runtime_endpoint_id" {
  description = "VPC Endpoint ID for Bedrock Agent Runtime. Null if enable_bedrock_agent = false."
  value       = var.enable_bedrock_agent ? aws_vpc_endpoint.bedrock_agent_runtime[0].id : null
}

output "cloudwatch_endpoint_id" {
  description = "VPC Endpoint ID for CloudWatch Metrics."
  value       = aws_vpc_endpoint.cloudwatch.id
}

output "cloudwatch_logs_endpoint_id" {
  description = "VPC Endpoint ID for CloudWatch Logs."
  value       = aws_vpc_endpoint.cloudwatch_logs.id
}

output "sts_endpoint_id" {
  description = "VPC Endpoint ID for STS."
  value       = aws_vpc_endpoint.sts.id
}
