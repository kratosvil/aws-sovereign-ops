# bedrock-privatelink — AI Context

## What this module does

Creates VPC Interface Endpoints (AWS PrivateLink) for Amazon Bedrock, CloudWatch, and STS.
After applying this module, resources inside the VPC can call these AWS services without
any traffic leaving the VPC — no NAT Gateway, no internet route required.

## Endpoints created

| Endpoint | Service name suffix | Purpose |
|----------|--------------------|---------||
| bedrock-runtime | bedrock-runtime | LLM inference — InvokeModel, InvokeModelWithResponseStream |
| bedrock | bedrock | Control plane — ListFoundationModels, GetFoundationModel |
| bedrock-agent-runtime | bedrock-agent-runtime | Optional — only if using Bedrock Agents API |
| cloudwatch | monitoring | Metrics API — GetMetricData, PutMetricData |
| cloudwatch-logs | logs | Log groups — GetLogEvents, FilterLogEvents |
| sts | sts | IAM role assumption — AssumeRole, GetCallerIdentity |

## Private DNS — critical behavior

All endpoints have `private_dns_enabled = true`. This means the standard AWS SDK endpoint
URLs (e.g., `bedrock-runtime.us-east-1.amazonaws.com`) resolve to private IPs inside the VPC.
No code changes are needed in the MCP Server — the SDK automatically uses the private endpoint.

## Security model

- One Security Group controls access to ALL endpoints in this module
- Only `allowed_sg_ids` can reach port 443 on the endpoints
- Pass `module.networking.sg_app_id` (MCP Server SG) as `allowed_sg_ids`
- No other resource in the VPC can reach the endpoints

## Usage pattern

```hcl
module "bedrock_privatelink" {
  source = "../../modules/bedrock-privatelink"

  project_name   = var.project_name
  vpc_id         = module.networking.vpc_id
  subnet_ids     = module.networking.subnet_private_ids
  allowed_sg_ids = [module.networking.sg_app_id]
}
```

## Cost

Each Interface Endpoint charges ~$0.01/hour per AZ + $0.01/GB data processed.
With 2 AZs and 5 endpoints: ~$0.10/hour = ~$1.75 for a 48-hour lab.

## Important constraints

- Interface Endpoints require subnets in at least 1 AZ — always pass private subnet IDs
- `private_dns_enabled = true` requires `enableDnsSupport = true` on the VPC (default in networking module)
- Region is resolved automatically via `data.aws_region.current` — never hardcode it
