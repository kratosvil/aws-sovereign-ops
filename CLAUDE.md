# aws-sovereign-ops — AI Context

## What this repo is

Zero-egress AI operations platform for AWS. An autonomous agent manages AWS infrastructure
(EKS, ECS, RDS) operating exclusively inside a private VPC — no traffic ever leaves to the internet.
Amazon Bedrock is reached via PrivateLink. Every destructive action requires human approval (HITL).
Full audit trail in CloudTrail.

## Architecture

```
VPC PRIVADA (zero-egress)
├── ECS Fargate          → MCP Server (Python) — the AI agent
├── Bedrock PrivateLink  → Claude / Nova — the LLM brain (no internet)
├── CloudWatch Alarms    → triggers on OOMKilled, latency, error rate
├── Lambda HITL          → sends APPROVE/REJECT notification to operator
├── CloudTrail           → immutable audit log (S3 WORM + KMS)
└── IAM least-privilege  → agent has no IAM/billing/root access
```

## Module dependency order

Always compose in this order:
1. networking (from tf-modules-forge)
2. iam-base (from tf-modules-forge)
3. s3-backend (from tf-modules-forge)
4. bedrock-privatelink (this repo)
5. cloudtrail-audit (this repo)
6. cloudwatch-alarms (this repo)
7. ecs-fargate (from tf-modules-forge) — deploys MCP Server
8. ecr (from tf-modules-forge) — MCP Server Docker image

## MCP Server tools

| Tool | Action | Requires HITL approval |
|------|--------|----------------------|
| `analyze_incident` | Read-only — pulls logs + metrics | No |
| `propose_fix` | Generates Terraform diff + risk assessment | No |
| `execute_approved` | Applies fix after human token | Yes — validates token |
| `rollback` | Reverts last apply | Yes — validates token |

## Security constraints (non-negotiable)

- Agent IAM role: no IAM write, no billing, no root — actions limited to EKS/ECS/RDS in this VPC
- MCP tools: read-only by default; write requires session token issued after HITL approval
- Bedrock traffic: VPC Interface Endpoint only — SG blocks all 0.0.0.0/0 egress
- CloudTrail: S3 WORM bucket — cannot be deleted or modified by the agent
- Circuit breaker: 2 consecutive failures → agent suspends and escalates

## tf-modules-forge dependency

This repo REUSES (does not duplicate) modules from kratosvil/tf-modules-forge:
- networking, iam-base, s3-backend, ecr, ecs-fargate, eks-cluster, rds-postgres

Never copy module code here. Reference via source = "github.com/kratosvil/tf-modules-forge//modules/X"

## Demo scenario

Pod OOMKilled in EKS → CloudWatch alarm → Lambda trigger → MCP Server activates →
Bedrock analyzes (via PrivateLink) → Fix proposed → Operator approves on Slack/email →
Agent executes kubectl patch + terraform apply → Validates metrics → CloudTrail entry created.
Total time: < 5 minutes.
