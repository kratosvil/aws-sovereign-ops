locals {
  tags = merge(
    {
      Project   = var.project_name
      Module    = "bedrock-privatelink"
      ManagedBy = "terraform"
    },
    var.tags
  )
}

data "aws_region" "current" {}

# ---------------------------------------------------------------------------
# Security Group — controls which resources can reach the VPC endpoints
# ---------------------------------------------------------------------------
resource "aws_security_group" "endpoints" {
  name        = "${var.project_name}-bedrock-endpoints"
  description = "Controls access to Bedrock, CloudWatch, and STS VPC Interface Endpoints"
  vpc_id      = var.vpc_id

  ingress {
    description     = "HTTPS from allowed SGs (MCP Server)"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = var.allowed_sg_ids
  }

  egress {
    description = "Allow all outbound (required for endpoint ENI health checks)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"] #tfsec:ignore:aws-ec2-no-public-egress-sgr
  }

  tags = merge(local.tags, { Name = "${var.project_name}-bedrock-endpoints" })
}

# ---------------------------------------------------------------------------
# Bedrock Runtime — LLM inference (InvokeModel / InvokeModelWithResponseStream)
# ---------------------------------------------------------------------------
resource "aws_vpc_endpoint" "bedrock_runtime" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.bedrock-runtime"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.subnet_ids
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = merge(local.tags, { Name = "${var.project_name}-bedrock-runtime" })
}

# ---------------------------------------------------------------------------
# Bedrock (control plane) — required for ListFoundationModels, GetFoundationModel
# ---------------------------------------------------------------------------
resource "aws_vpc_endpoint" "bedrock" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.bedrock"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.subnet_ids
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = merge(local.tags, { Name = "${var.project_name}-bedrock" })
}

# ---------------------------------------------------------------------------
# Bedrock Agent Runtime — optional, for Bedrock Agents API
# ---------------------------------------------------------------------------
resource "aws_vpc_endpoint" "bedrock_agent_runtime" {
  count = var.enable_bedrock_agent ? 1 : 0

  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.bedrock-agent-runtime"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.subnet_ids
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = merge(local.tags, { Name = "${var.project_name}-bedrock-agent-runtime" })
}

# ---------------------------------------------------------------------------
# CloudWatch Monitoring — metrics and logs access from within the VPC
# ---------------------------------------------------------------------------
resource "aws_vpc_endpoint" "cloudwatch" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.monitoring"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.subnet_ids
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = merge(local.tags, { Name = "${var.project_name}-cloudwatch" })
}

# ---------------------------------------------------------------------------
# CloudWatch Logs — log group reads/writes from within the VPC
# ---------------------------------------------------------------------------
resource "aws_vpc_endpoint" "cloudwatch_logs" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.logs"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.subnet_ids
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = merge(local.tags, { Name = "${var.project_name}-cloudwatch-logs" })
}

# ---------------------------------------------------------------------------
# ECR API — authentication token for image pulls (GetAuthorizationToken)
# ---------------------------------------------------------------------------
resource "aws_vpc_endpoint" "ecr_api" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.ecr.api"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.subnet_ids
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = merge(local.tags, { Name = "${var.project_name}-ecr-api" })
}

# ---------------------------------------------------------------------------
# ECR DKR — Docker image layer pull
# ---------------------------------------------------------------------------
resource "aws_vpc_endpoint" "ecr_dkr" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.ecr.dkr"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.subnet_ids
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = merge(local.tags, { Name = "${var.project_name}-ecr-dkr" })
}

# ---------------------------------------------------------------------------
# SNS — publish HITL notification emails to operator
# ---------------------------------------------------------------------------
resource "aws_vpc_endpoint" "sns" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.sns"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.subnet_ids
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = merge(local.tags, { Name = "${var.project_name}-sns" })
}

# ---------------------------------------------------------------------------
# Lambda — execute_approved calls lambda:UpdateFunctionConfiguration etc.
# ---------------------------------------------------------------------------
resource "aws_vpc_endpoint" "lambda" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.lambda"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.subnet_ids
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = merge(local.tags, { Name = "${var.project_name}-lambda" })
}

# ---------------------------------------------------------------------------
# STS — IAM role assumption without internet
# ---------------------------------------------------------------------------
resource "aws_vpc_endpoint" "sts" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.sts"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.subnet_ids
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = merge(local.tags, { Name = "${var.project_name}-sts" })
}
