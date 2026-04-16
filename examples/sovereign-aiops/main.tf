# ============================================================
# sovereign-aiops — full stack demo
# Assembles all modules in dependency order.
# Read CLAUDE.md at the repo root for architecture context.
# ============================================================

# ------------------------------------------------------------
# 1. Networking — VPC, private subnets, security groups
#    Source: tf-modules-forge (never duplicated here)
# ------------------------------------------------------------
module "networking" {
  source = "github.com/kratosvil/tf-modules-forge//modules/networking?ref=main"

  project_name         = var.project_name
  vpc_cidr             = var.vpc_cidr
  private_subnet_cidrs = var.private_subnet_cidrs

  # Zero-egress: no public subnets, no NAT, no IGW for app traffic.
  # Bedrock/CloudWatch/STS are reached via PrivateLink (module below).
  enable_nat_gateway = false
}

# ------------------------------------------------------------
# 2. IAM base — agent execution role (least-privilege)
#    No IAM write, no billing, no root. EKS/ECS/RDS only.
# ------------------------------------------------------------
module "iam_base" {
  source = "github.com/kratosvil/tf-modules-forge//modules/iam-base?ref=main"

  project_name = var.project_name
}

# ------------------------------------------------------------
# 3. ECR — container registry for the MCP Server image
# ------------------------------------------------------------
module "ecr" {
  source = "github.com/kratosvil/tf-modules-forge//modules/ecr?ref=main"

  project_name    = var.project_name
  repository_name = "${var.project_name}-mcp-server"
}

# ------------------------------------------------------------
# 4. Bedrock PrivateLink — VPC endpoints for Bedrock, CW, STS
#    Depends on: networking
# ------------------------------------------------------------
module "bedrock_privatelink" {
  source = "../../modules/bedrock-privatelink"

  project_name   = var.project_name
  vpc_id         = module.networking.vpc_id
  subnet_ids     = module.networking.subnet_private_ids
  allowed_sg_ids = [module.networking.sg_app_id]
}

# ------------------------------------------------------------
# 5. CloudTrail audit — immutable WORM log (S3 + KMS)
#    No VPC dependency — global service.
# ------------------------------------------------------------
module "cloudtrail_audit" {
  source = "../../modules/cloudtrail-audit"

  project_name           = var.project_name
  retention_days         = var.cloudtrail_retention_days
  enable_cloudwatch_logs = true
}

# ------------------------------------------------------------
# 6. ECS Fargate — runs the MCP Server container
#    Depends on: networking, iam_base, ecr
# ------------------------------------------------------------
module "ecs_fargate" {
  source = "github.com/kratosvil/tf-modules-forge//modules/ecs-fargate?ref=main"

  project_name     = var.project_name
  vpc_id           = module.networking.vpc_id
  subnet_ids       = module.networking.subnet_private_ids
  security_group_id = module.networking.sg_app_id
  execution_role_arn = module.iam_base.execution_role_arn
  task_role_arn    = module.iam_base.task_role_arn

  container_name   = "mcp-server"
  container_image  = "${module.ecr.repository_url}:latest"
  container_port   = var.mcp_server_port
  cpu              = var.mcp_server_cpu
  memory           = var.mcp_server_memory

  # Environment variables available inside the MCP Server container.
  # Bedrock endpoint resolves to private IP via PrivateLink — no URL changes needed.
  environment_variables = {
    AWS_REGION        = var.aws_region
    PROJECT_NAME      = var.project_name
    HITL_SNS_TOPIC    = module.cloudwatch_alarms.sns_topic_arn
    CLOUDTRAIL_BUCKET = module.cloudtrail_audit.s3_bucket_id
  }
}

# ------------------------------------------------------------
# 7. HITL Notifier — Lambda + API Gateway + SNS Email subscription
#    Depends on: cloudwatch_alarms (sns_topic_arn), ecs_fargate (alb url)
# ------------------------------------------------------------
module "hitl_notifier" {
  source = "../../lambda/hitl-notifier"

  project_name   = var.project_name
  sns_topic_arn  = module.cloudwatch_alarms.sns_topic_arn
  mcp_server_url = "http://${module.ecs_fargate.alb_dns_name}"
  operator_email = var.operator_email
}

# ------------------------------------------------------------
# 8. CloudWatch alarms — any alarm type triggers the flow
#    Depends on: ecs_fargate, hitl_notifier
# ------------------------------------------------------------
module "cloudwatch_alarms" {
  source = "../../modules/cloudwatch-alarms"

  project_name     = var.project_name
  ecs_cluster_name = module.ecs_fargate.cluster_name
  ecs_service_name = module.ecs_fargate.service_name
  alb_arn_suffix   = module.ecs_fargate.alb_arn_suffix
  alarm_email      = var.operator_email

  # EventBridge routes alarm state changes to the HITL Lambda.
  mcp_server_lambda_arn = module.hitl_notifier.lambda_arn
}
