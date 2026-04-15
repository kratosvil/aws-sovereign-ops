# cloudtrail-audit — AI Context

## What this module does

Creates an immutable, encrypted audit trail for every AWS API call made by the
sovereign-aiops agent. Logs are stored in an S3 bucket with Object Lock (WORM) —
they cannot be deleted or modified by anyone, including the agent itself.

## Resources created

| Resource | Purpose |
|----------|---------|
| KMS key + alias | Encrypts all CloudTrail events. Agent role has no kms:Decrypt |
| S3 bucket (Object Lock WORM) | Stores logs. Retention enforced at storage level |
| S3 bucket policy | Denies s3:DeleteObject for all principals. Allows CloudTrail write only |
| CloudTrail (multi-region) | Captures all API calls across all regions |
| CloudWatch Log Group (optional) | Real-time event stream for alerting |
| IAM role for CloudTrail → CW | Least-privilege: only CreateLogStream + PutLogEvents |

## Security guarantees

- `force_destroy = false` on the S3 bucket — `terraform destroy` will fail if logs exist
- Object Lock GOVERNANCE mode — requires special permissions to override, which the agent does not have
- `DenyDeleteObject` bucket policy — explicit deny overrides any allow
- KMS key — agent role is not in the key policy, cannot decrypt logs
- `enable_log_file_validation = true` — CloudTrail signs each log file, detects tampering
- Multi-region trail — captures activity in all regions, not just the deployment region

## What gets logged

Every AWS API call made by the agent: kubectl calls via EKS API, terraform apply actions,
Lambda invocations, SNS publishes, S3 reads. Each entry includes:
- eventTime, eventName, eventSource
- userIdentity (the agent IAM role ARN)
- requestParameters (what was changed)
- sourceIPAddress (private IP inside VPC)

## Usage pattern

```hcl
module "cloudtrail_audit" {
  source = "../../modules/cloudtrail-audit"

  project_name   = var.project_name
  retention_days = 90
}
```

No VPC dependency — CloudTrail is a global service, does not need VPC inputs.

## Important constraints

- S3 bucket name includes account ID to guarantee global uniqueness
- `force_destroy = false` is intentional — do NOT change this for production
- GOVERNANCE mode Object Lock can be bypassed by accounts with s3:BypassGovernanceRetention
  permission — for stricter compliance use COMPLIANCE mode (cannot be bypassed by anyone)
- The KMS key has a 7-day deletion window — it cannot be deleted immediately
