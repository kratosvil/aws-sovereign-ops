# ============================================================
# HITL Notifier — Lambda + API Gateway + SNS Email subscription
#
# Wiring in examples/sovereign-aiops/main.tf:
#   module "hitl_notifier" {
#     source         = "../../lambda/hitl-notifier"
#     project_name   = var.project_name
#     sns_topic_arn  = module.cloudwatch_alarms.sns_topic_arn
#     mcp_server_url = "http://${module.ecs_fargate.alb_dns_name}"
#     operator_email = var.operator_email
#   }
# ============================================================

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals {
  name = "${var.project_name}-hitl-notifier"
}

# ------------------------------------------------------------------
# Variables
# ------------------------------------------------------------------

variable "project_name" {
  description = "Resource name prefix."
  type        = string
}

variable "sns_topic_arn" {
  description = "SNS topic ARN from module.cloudwatch_alarms.sns_topic_arn."
  type        = string
}

variable "mcp_server_url" {
  description = "Internal ALB URL of the MCP Server (http://alb-dns-name)."
  type        = string
}

variable "operator_email" {
  description = "Email address that receives APPROVAL REQUIRED notifications."
  type        = string
}

variable "tags" {
  description = "Additional tags."
  type        = map(string)
  default     = {}
}

# ------------------------------------------------------------------
# Lambda package (inline zip of handler.py + formatter.py)
# ------------------------------------------------------------------

data "archive_file" "lambda_zip" {
  type        = "zip"
  output_path = "${path.module}/hitl_notifier.zip"
  source_dir  = path.module
  excludes    = ["template.tf", "hitl_notifier.zip"]
}

# ------------------------------------------------------------------
# IAM — Lambda execution role
# ------------------------------------------------------------------

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = local.name
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "basic_execution" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# ------------------------------------------------------------------
# Lambda function
# ------------------------------------------------------------------

resource "aws_lambda_function" "hitl_notifier" {
  function_name    = local.name
  role             = aws_iam_role.lambda.arn
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  handler          = "handler.lambda_handler"
  runtime          = "python3.11"
  timeout          = 30

  environment {
    variables = {
      MCP_SERVER_URL = var.mcp_server_url
      API_BASE_URL   = "https://${aws_api_gateway_rest_api.hitl.id}.execute-api.${data.aws_region.current.name}.amazonaws.com/prod"
    }
  }

  tags = var.tags
}

# Allow SNS to invoke Lambda (future extension — SNS trigger on Lambda directly)
resource "aws_lambda_permission" "allow_sns" {
  statement_id  = "AllowSNSInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.hitl_notifier.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = var.sns_topic_arn
}

# Allow API Gateway to invoke Lambda
resource "aws_lambda_permission" "allow_apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.hitl_notifier.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.hitl.execution_arn}/*/*"
}

# ------------------------------------------------------------------
# SNS Email subscription — delivers plain text notification directly
# ------------------------------------------------------------------

resource "aws_sns_topic_subscription" "operator_email" {
  topic_arn = var.sns_topic_arn
  protocol  = "email"
  endpoint  = var.operator_email
}

# ------------------------------------------------------------------
# API Gateway — /approve and /reject endpoints (public, HTTPS)
# ------------------------------------------------------------------

resource "aws_api_gateway_rest_api" "hitl" {
  name        = local.name
  description = "HITL approval/reject endpoints for sovereign-aiops operator."
  tags        = var.tags
}

# /approve
resource "aws_api_gateway_resource" "approve" {
  rest_api_id = aws_api_gateway_rest_api.hitl.id
  parent_id   = aws_api_gateway_rest_api.hitl.root_resource_id
  path_part   = "approve"
}

resource "aws_api_gateway_method" "approve_get" {
  rest_api_id   = aws_api_gateway_rest_api.hitl.id
  resource_id   = aws_api_gateway_resource.approve.id
  http_method   = "GET"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "approve" {
  rest_api_id             = aws_api_gateway_rest_api.hitl.id
  resource_id             = aws_api_gateway_resource.approve.id
  http_method             = aws_api_gateway_method.approve_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.hitl_notifier.invoke_arn
}

# /reject
resource "aws_api_gateway_resource" "reject" {
  rest_api_id = aws_api_gateway_rest_api.hitl.id
  parent_id   = aws_api_gateway_rest_api.hitl.root_resource_id
  path_part   = "reject"
}

resource "aws_api_gateway_method" "reject_get" {
  rest_api_id   = aws_api_gateway_rest_api.hitl.id
  resource_id   = aws_api_gateway_resource.reject.id
  http_method   = "GET"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "reject" {
  rest_api_id             = aws_api_gateway_rest_api.hitl.id
  resource_id             = aws_api_gateway_resource.reject.id
  http_method             = aws_api_gateway_method.reject_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.hitl_notifier.invoke_arn
}

# Deploy
resource "aws_api_gateway_deployment" "hitl" {
  rest_api_id = aws_api_gateway_rest_api.hitl.id

  depends_on = [
    aws_api_gateway_integration.approve,
    aws_api_gateway_integration.reject,
  ]

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "prod" {
  rest_api_id   = aws_api_gateway_rest_api.hitl.id
  deployment_id = aws_api_gateway_deployment.hitl.id
  stage_name    = "prod"
  tags          = var.tags
}

# ------------------------------------------------------------------
# Outputs
# ------------------------------------------------------------------

output "lambda_arn" {
  description = "Lambda ARN — pass to cloudwatch_alarms as mcp_server_lambda_arn."
  value       = aws_lambda_function.hitl_notifier.arn
}

output "api_base_url" {
  description = "Public base URL for approve/reject endpoints."
  value       = "https://${aws_api_gateway_rest_api.hitl.id}.execute-api.${data.aws_region.current.name}.amazonaws.com/prod"
}

output "approve_url_template" {
  description = "Approve URL template — replace placeholders."
  value       = "https://${aws_api_gateway_rest_api.hitl.id}.execute-api.${data.aws_region.current.name}.amazonaws.com/prod/approve?incident_id=INCIDENT_ID&token=TOKEN&approved_by=NAME"
}
