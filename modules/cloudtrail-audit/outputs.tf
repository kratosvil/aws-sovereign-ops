output "trail_arn" {
  description = "ARN of the CloudTrail trail."
  value       = aws_cloudtrail.audit.arn
}

output "trail_name" {
  description = "Name of the CloudTrail trail."
  value       = aws_cloudtrail.audit.name
}

output "s3_bucket_id" {
  description = "S3 bucket ID where CloudTrail logs are stored (WORM protected)."
  value       = aws_s3_bucket.cloudtrail.id
}

output "s3_bucket_arn" {
  description = "S3 bucket ARN for CloudTrail logs."
  value       = aws_s3_bucket.cloudtrail.arn
}

output "kms_key_arn" {
  description = "KMS key ARN used to encrypt CloudTrail logs. Grant kms:Decrypt to roles that need to read logs."
  value       = aws_kms_key.cloudtrail.arn
}

output "kms_key_id" {
  description = "KMS key ID."
  value       = aws_kms_key.cloudtrail.key_id
}

output "cloudwatch_log_group_name" {
  description = "CloudWatch Log Group name for real-time trail events. Null if enable_cloudwatch_logs = false."
  value       = var.enable_cloudwatch_logs ? aws_cloudwatch_log_group.cloudtrail[0].name : null
}

output "cloudwatch_log_group_arn" {
  description = "CloudWatch Log Group ARN. Null if enable_cloudwatch_logs = false."
  value       = var.enable_cloudwatch_logs ? aws_cloudwatch_log_group.cloudtrail[0].arn : null
}
