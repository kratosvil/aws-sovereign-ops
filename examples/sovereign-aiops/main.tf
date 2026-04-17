# ============================================================
# sovereign-aiops — full stack demo
# Assembles all modules in dependency order.
# Read CLAUDE.md at the repo root for architecture context.
# ============================================================

# ------------------------------------------------------------
# 0. SNS Topic — created first to break circular dependencies.
#    Both ecs-fargate (env var) and cloudwatch-alarms reference
#    this ARN. Creating it here as a standalone resource allows
#    all downstream modules to consume it without cycles.
# ------------------------------------------------------------
resource "aws_sns_topic" "alarms" {
  name              = "${var.project_name}-alarms"
  kms_master_key_id = "alias/aws/sns"

  tags = {
    Project   = var.project_name
    ManagedBy = "terraform"
  }
}

# ------------------------------------------------------------
# 1. Networking — VPC, public + private subnets, security groups
#    Source: tf-modules-forge (never duplicated here)
#    Zero-egress: NAT disabled — Bedrock/CW/STS via PrivateLink.
# ------------------------------------------------------------
module "networking" {
  source = "github.com/kratosvil/tf-modules-forge//modules/networking?ref=main"

  project_name       = var.project_name
  vpc_cidr           = var.vpc_cidr
  enable_nat_gateway = false
}

# ------------------------------------------------------------
# 2. ECR — container registry for the MCP Server image
# ------------------------------------------------------------
module "ecr" {
  source = "github.com/kratosvil/tf-modules-forge//modules/ecr?ref=main"

  project_name    = var.project_name
  repository_name = "${var.project_name}-mcp-server"
}

# ------------------------------------------------------------
# 3a. S3 Gateway Endpoint — required for ECR image layer pulls.
#     Docker image layers are stored in S3. Without this endpoint
#     tasks in private subnets cannot pull images (no NAT).
#     Gateway endpoints are free and attach to route tables.
# ------------------------------------------------------------
data "aws_route_tables" "vpc" {
  vpc_id = module.networking.vpc_id
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = module.networking.vpc_id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = data.aws_route_tables.vpc.ids

  tags = {
    Name      = "${var.project_name}-s3"
    Project   = var.project_name
    ManagedBy = "terraform"
  }
}

# ------------------------------------------------------------
# 3b. Bedrock PrivateLink — VPC endpoints for Bedrock, CW, STS,
#     ECR API, ECR DKR. Depends on: networking
# ------------------------------------------------------------
module "bedrock_privatelink" {
  source = "../../modules/bedrock-privatelink"

  project_name   = var.project_name
  vpc_id         = module.networking.vpc_id
  subnet_ids     = module.networking.subnet_private_ids
  allowed_sg_ids = [module.networking.sg_app_id]
}

# ------------------------------------------------------------
# 4. CloudTrail audit — immutable WORM log (S3 + KMS)
#    No VPC dependency — global service.
# ------------------------------------------------------------
module "cloudtrail_audit" {
  source = "../../modules/cloudtrail-audit"

  project_name           = var.project_name
  retention_days         = var.cloudtrail_retention_days
  enable_cloudwatch_logs = true
}

# ------------------------------------------------------------
# 5. ECS Fargate — runs the MCP Server container
#    Depends on: networking, ecr, bedrock_privatelink
#    Uses aws_sns_topic.alarms.arn directly (no circular dep).
# ------------------------------------------------------------
module "ecs_fargate" {
  source = "github.com/kratosvil/tf-modules-forge//modules/ecs-fargate?ref=main"

  project_name       = var.project_name
  vpc_id             = module.networking.vpc_id
  subnet_public_ids  = module.networking.subnet_public_ids
  subnet_private_ids = module.networking.subnet_private_ids
  sg_alb_id          = module.networking.sg_alb_id
  sg_app_id          = module.networking.sg_app_id

  container_image  = "${module.ecr.repository_url}:latest"
  container_port   = var.mcp_server_port
  container_cpu    = var.mcp_server_cpu
  container_memory = var.mcp_server_memory

  container_environment = [
    { name = "AWS_REGION",        value = var.aws_region },
    { name = "PROJECT_NAME",      value = var.project_name },
    { name = "HITL_SNS_TOPIC",    value = aws_sns_topic.alarms.arn },
    { name = "CLOUDTRAIL_BUCKET", value = module.cloudtrail_audit.s3_bucket_id },
    { name = "BEDROCK_MODEL_ID",  value = "us.anthropic.claude-haiku-4-5-20251001-v1:0" },
    { name = "HITL_TOKEN_SECRET", value = var.hitl_token_secret },
    { name = "API_BASE_URL",      value = "https://788nqj8wtg.execute-api.us-east-1.amazonaws.com/prod" },
  ]

  depends_on = [module.bedrock_privatelink]
}

# ------------------------------------------------------------
# 6. HITL Notifier — Lambda + API Gateway + SNS Email
#    Depends on: aws_sns_topic.alarms, ecs_fargate (alb_dns_name)
# ------------------------------------------------------------
module "hitl_notifier" {
  source = "../../lambda/hitl-notifier"

  project_name   = var.project_name
  sns_topic_arn  = aws_sns_topic.alarms.arn
  mcp_server_url = "http://${module.ecs_fargate.alb_dns_name}"
  operator_email = var.operator_email
}

# ------------------------------------------------------------
# 6b. IAM policy for ECS task role — grants MCP Server permissions
#     to call Bedrock, read CloudWatch metrics/logs, and publish to SNS.
#     The task role (sovereign-aiops-ecs-task) is created by ecs-fargate.
# ------------------------------------------------------------
resource "aws_iam_role_policy" "mcp_server" {
  name = "${var.project_name}-mcp-server-policy"
  role = split("/", module.ecs_fargate.task_role_arn)[1]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BedrockInvoke"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = [
          "arn:aws:bedrock:*:${data.aws_caller_identity.current.account_id}:inference-profile/*",
          "arn:aws:bedrock:*::foundation-model/*"
        ]
      },
      {
        Sid    = "CloudWatchMetrics"
        Effect = "Allow"
        Action = [
          "cloudwatch:GetMetricData",
          "cloudwatch:GetMetricStatistics",
          "cloudwatch:DescribeAlarms",
          "cloudwatch:DescribeAlarmHistory"
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:GetLogEvents",
          "logs:FilterLogEvents",
          "logs:DescribeLogStreams",
          "logs:DescribeLogGroups",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "*"
      },
      {
        Sid    = "SNSPublish"
        Effect = "Allow"
        Action = ["sns:Publish"]
        Resource = [aws_sns_topic.alarms.arn]
      },
      {
        Sid    = "RemediationActions"
        Effect = "Allow"
        Action = [
          "lambda:UpdateFunctionConfiguration",
          "lambda:UpdateFunctionCode",
          "lambda:GetFunctionConfiguration",
          "lambda:PutFunctionConcurrency",
          "lambda:DeleteFunctionConcurrency",
          "lambda:GetFunctionConcurrency",
          "ecs:UpdateService",
          "ecs:DescribeServices",
          "rds:ModifyDBInstance",
          "rds:DescribeDBInstances"
        ]
        Resource = "*"
      }
    ]
  })
}

data "aws_caller_identity" "current" {}

# ------------------------------------------------------------
# 7. CloudWatch alarms — any alarm type triggers the flow
#    Uses sns_topic_arn (standalone resource above) to avoid
#    circular dependency. Depends on: ecs_fargate, hitl_notifier
# ------------------------------------------------------------
module "cloudwatch_alarms" {
  source = "../../modules/cloudwatch-alarms"

  project_name          = var.project_name
  sns_topic_arn         = aws_sns_topic.alarms.arn
  enable_alb_alarms     = true
  enable_ecs_alarms     = true
  alb_arn_suffix        = module.ecs_fargate.alb_arn_suffix
  ecs_cluster_name      = module.ecs_fargate.cluster_name
  ecs_service_name      = module.ecs_fargate.service_name
  alarm_email           = var.operator_email
  mcp_server_lambda_arn = module.hitl_notifier.lambda_arn
}
