# ==============================================================
# cloudtrail-audit — Immutable audit log for agent actions
# Layer: ORGANIZATION
# ==============================================================

locals {
  tags = merge(
    {
      Project   = var.project_name
      Module    = "cloudtrail-audit"
      ManagedBy = "terraform"
    },
    var.tags
  )
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# --------------------------------------------------------------
# KMS KEY — dedicated key for CloudTrail encryption
# The agent role does NOT have kms:Decrypt on this key
# --------------------------------------------------------------
resource "aws_kms_key" "cloudtrail" {
  description             = "${var.project_name} CloudTrail audit log encryption key"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RootFullAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "CloudTrailEncrypt"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action   = "kms:GenerateDataKey*"
        Resource = "*"
        Condition = {
          StringLike = {
            "kms:EncryptionContext:aws:cloudtrail:arn" = "arn:aws:cloudtrail:*:${data.aws_caller_identity.current.account_id}:trail/*"
          }
        }
      },
      {
        Sid    = "CloudTrailDescribeKey"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action   = "kms:DescribeKey"
        Resource = "*"
      }
    ]
  })

  tags = merge(local.tags, { Name = "${var.project_name}-cloudtrail-key" })
}

resource "aws_kms_alias" "cloudtrail" {
  name          = "alias/${var.project_name}-cloudtrail"
  target_key_id = aws_kms_key.cloudtrail.key_id
}

# --------------------------------------------------------------
# S3 BUCKET — WORM storage for CloudTrail logs
# --------------------------------------------------------------
resource "aws_s3_bucket" "cloudtrail" {
  bucket        = "${var.project_name}-cloudtrail-${data.aws_caller_identity.current.account_id}"
  force_destroy = false # Prevents accidental deletion via terraform destroy

  object_lock_enabled = true

  tags = merge(local.tags, { Name = "${var.project_name}-cloudtrail" })
}

resource "aws_s3_bucket_versioning" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_object_lock_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  rule {
    default_retention {
      mode = "GOVERNANCE"
      days = var.retention_days
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.cloudtrail.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "cloudtrail" {
  bucket                  = aws_s3_bucket.cloudtrail.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DenyNonTLS"
        Effect = "Deny"
        Principal = {
          AWS = "*"
        }
        Action   = "s3:*"
        Resource = [
          aws_s3_bucket.cloudtrail.arn,
          "${aws_s3_bucket.cloudtrail.arn}/*"
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      },
      {
        Sid    = "CloudTrailAclCheck"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action   = "s3:GetBucketAcl"
        Resource = aws_s3_bucket.cloudtrail.arn
        Condition = {
          StringEquals = {
            "aws:SourceArn" = "arn:aws:cloudtrail:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:trail/${var.project_name}-audit"
          }
        }
      },
      {
        Sid    = "CloudTrailWrite"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.cloudtrail.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl" = "bucket-owner-full-control"
            "aws:SourceArn" = "arn:aws:cloudtrail:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:trail/${var.project_name}-audit"
          }
        }
      },
      {
        Sid    = "DenyDeleteObject"
        Effect = "Deny"
        Principal = {
          AWS = "*"
        }
        Action = [
          "s3:DeleteObject",
          "s3:DeleteObjectVersion"
        ]
        Resource = "${aws_s3_bucket.cloudtrail.arn}/*"
      }
    ]
  })
}

# --------------------------------------------------------------
# CLOUDWATCH LOG GROUP — real-time event streaming (optional)
# --------------------------------------------------------------
resource "aws_cloudwatch_log_group" "cloudtrail" {
  count = var.enable_cloudwatch_logs ? 1 : 0

  name              = "/aws/cloudtrail/${var.project_name}"
  retention_in_days = var.cloudwatch_log_retention_days
  kms_key_id        = aws_kms_key.cloudtrail.arn

  tags = local.tags
}

resource "aws_iam_role" "cloudtrail_cw" {
  count = var.enable_cloudwatch_logs ? 1 : 0

  name = "${var.project_name}-cloudtrail-cw-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "cloudtrail.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "cloudtrail_cw" {
  count = var.enable_cloudwatch_logs ? 1 : 0

  name = "${var.project_name}-cloudtrail-cw-policy"
  role = aws_iam_role.cloudtrail_cw[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ]
      Resource = "${aws_cloudwatch_log_group.cloudtrail[0].arn}:*"
    }]
  })
}

# --------------------------------------------------------------
# CLOUDTRAIL — multi-region trail with S3 + optional CW
# --------------------------------------------------------------
resource "aws_cloudtrail" "audit" {
  name                          = "${var.project_name}-audit"
  s3_bucket_name                = aws_s3_bucket.cloudtrail.id
  include_global_service_events = true
  is_multi_region_trail         = true
  enable_log_file_validation    = true
  kms_key_id                    = aws_kms_key.cloudtrail.arn

  cloud_watch_logs_group_arn = var.enable_cloudwatch_logs ? "${aws_cloudwatch_log_group.cloudtrail[0].arn}:*" : null
  cloud_watch_logs_role_arn  = var.enable_cloudwatch_logs ? aws_iam_role.cloudtrail_cw[0].arn : null

  event_selector {
    read_write_type           = "All"
    include_management_events = true

    data_resource {
      type   = "AWS::S3::Object"
      values = ["arn:aws:s3:::"]
    }
  }

  depends_on = [aws_s3_bucket_policy.cloudtrail]

  tags = merge(local.tags, { Name = "${var.project_name}-audit" })
}
