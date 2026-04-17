# aws-sovereign-ops — AI Context

## What this repo is

Zero-egress autonomous incident response platform for AWS. An MCP Server (Python, ECS Fargate)
receives ANY CloudWatch alarm, investigates it using Bedrock via PrivateLink, proposes a fix,
requests human approval (HITL), executes the approved fix, validates the result, and rolls back
if validation fails. Full audit trail in CloudTrail. No traffic ever leaves the VPC.

## Core design principle — ANY alarm, any resource type

The system is NOT limited to OOMKilled or Kubernetes failures.
It handles ANY CloudWatch alarm: EKS pods, ECS services, RDS instances, Lambda functions,
ALB latency/errors, DynamoDB throttles, or any custom metric alarm.

Bedrock (the LLM) is the reasoning engine — it determines the root cause and fix based on
the logs and metrics collected. The code does not hardcode failure types.

## Architecture

```
VPC PRIVADA (zero-egress)
├── ECS Fargate          → MCP Server (Python) — orchestrates the full flow
├── Bedrock PrivateLink  → Claude / Nova — reasoning engine (no internet)
├── CloudWatch Alarms    → ANY alarm type triggers the flow
├── Lambda HITL          → sends APPROVE/REJECT notification to operator
├── CloudTrail           → immutable audit log (S3 WORM + KMS)
└── IAM least-privilege  → agent has no IAM/billing/root access
```

## Incident response flow

```
1. ANY CloudWatch alarm fires (OOMKilled, 5XX, RDS latency, Lambda timeout, etc.)
2. EventBridge routes alarm event → POST /alarm on MCP Server
3. MCP Server starts Bedrock agentic loop (investigation phase):
   - Bedrock calls analyze_incident() → pulls CW logs + metrics for the resource
   - Bedrock calls propose_fix()      → formalizes root cause + fix + risk level
4. MCP Server calls Lambda HITL → operator receives notification with APPROVE token
5. Operator calls POST /approve with token
6. MCP Server starts Bedrock agentic loop (execution phase):
   - Bedrock calls execute_approved(token) → runs kubectl and/or terraform
   - Bedrock calls validate_fix()          → checks post-fix metrics
   - If validation fails: Bedrock calls rollback(token)
7. Every action written to CloudTrail via structured JSON logs
```

## MCP Server tools

| Tool | Phase | Requires HITL token | Bedrock calls this |
|------|-------|--------------------|--------------------|
| `analyze_incident` | Investigation | No | Yes |
| `propose_fix` | Investigation | No | Yes — formalizes Bedrock's own diagnosis |
| `execute_approved` | Execution | Yes | Yes |
| `validate_fix` | Execution | No | Yes |
| `rollback` | Execution | Yes | Yes |

## analyze_incident — resource types supported

| resource_type | Metrics pulled | Log groups |
|---|---|---|
| `eks_pod` | memory_util, cpu_util, restarts | /aws/eks/{project}/containers |
| `ecs_service` | CPUUtilization, MemoryUtilization | /ecs/{project} |
| `rds` | CPU, connections, read/write latency, freeable memory | /aws/rds/instance/{resource}/* |
| `lambda` | errors, duration, throttles, concurrent executions | /aws/lambda/{resource} |
| `alb` | 5XX count, p99 latency, request count, healthy hosts | /aws/applicationelb/{project} |
| `generic` | log search only | /aws/{resource} |

To add a new resource type: add entries to METRIC_QUERIES and LOG_GROUPS in
mcp-server/tools/analyze_incident.py. No other files need to change.

## Module dependency order

Always compose in this order:
1. networking (from tf-modules-forge)
2. ecr (from tf-modules-forge)
3. bedrock-privatelink (this repo)
4. cloudtrail-audit (this repo)
5. ecs-fargate (from tf-modules-forge) — deploys MCP Server, manages its own IAM roles
6. hitl-notifier (this repo — lambda/)
7. cloudwatch-alarms (this repo) — requires sns_topic_arn and mcp_server_lambda_arn

Note: iam-base is NOT used. ecs-fargate creates task + execution IAM roles internally.

## Repository structure

```
aws-sovereign-ops/
├── modules/
│   ├── bedrock-privatelink/   # VPC Interface Endpoints: Bedrock, CW, STS
│   ├── cloudtrail-audit/      # CloudTrail + S3 WORM + KMS
│   └── cloudwatch-alarms/     # Pre-built alarms + EventBridge routing
├── examples/
│   └── sovereign-aiops/       # Full stack — assembles all modules
├── mcp-server/
│   ├── server.py              # FastAPI: /alarm /approve /reject /health
│   ├── orchestrator.py        # Agentic loop, circuit breaker, 2 phases
│   ├── models.py              # AlarmEvent, FixProposal, etc.
│   ├── services/
│   │   ├── bedrock.py         # Bedrock converse API with tool_use
│   │   ├── hitl.py            # HMAC token generation/validation + SNS
│   │   └── audit.py           # Structured JSON logs → CloudTrail
│   └── tools/
│       ├── analyze_incident.py  # Generic — any resource type
│       ├── propose_fix.py       # Formalizes Bedrock's diagnosis
│       ├── execute_approved.py  # kubectl + terraform with token validation
│       ├── validate_fix.py      # Post-fix metrics check
│       └── rollback.py          # Revert on failed validation
├── lambda/
│   └── hitl-notifier/         # APPROVE/REJECT notification to operator
├── scripts/
│   └── demo.sh                # Simulates alarm → full remediation loop
└── docs/
    └── architecture.md
```

## tf-modules-forge dependency

This repo REUSES (never duplicates) modules from kratosvil/tf-modules-forge:
- networking, iam-base, s3-backend, ecr, ecs-fargate

Reference via: source = "github.com/kratosvil/tf-modules-forge//modules/X"

## Security constraints (non-negotiable)

- Agent IAM role: no IAM write, no billing, no root
- MCP tools: read-only by default; write requires HITL token (HMAC-SHA256, TTL 15 min)
- Bedrock traffic: VPC Interface Endpoint only — no NAT, no IGW
- CloudTrail: S3 WORM — cannot be deleted or modified by the agent
- Circuit breaker: 2 consecutive tool failures → agent suspends and escalates

## Build status

| Component | Status |
|-----------|--------|
| modules/bedrock-privatelink | Done |
| modules/cloudtrail-audit | Done |
| modules/cloudwatch-alarms | Done |
| examples/sovereign-aiops | Done — deployed to AWS 2026-04-16/17 |
| mcp-server/ | Done — image in ECR |
| lambda/hitl-notifier | Done — deployed to AWS |
| scripts/demo.sh | Done — 4/4 local tests passed |
| docs/architecture.md | Done |

## e2e test scenarios validated (v1)

| # | Scenario | Lambda | Fix Applied | Incident |
|---|----------|--------|-------------|---------|
| 1 | Lambda timeout | sovereign-aiops-timeout-test | timeout 3s → 60s | eb0a61b5 |
| 2 | Lambda throttled | sovereign-aiops-throttle-test | delete-function-concurrency | fd5569c2 |
| 3 | Lambda OOM | sovereign-aiops-oom-test | memory 128MB → 256MB | b87000cc |
| 4 | Lambda errors | sovereign-aiops-error-test | diagnosis + medium risk | 5125a7c4 |
| 5 | ECS desired=0 | — | deferred to v2 (SPOF: MCP Server is in same ECS) | — |

## Live AWS resources (us-east-1, account 805778285334)

| Resource | Value |
|----------|-------|
| ECS Cluster | sovereign-aiops-cluster |
| ECS Service | sovereign-aiops-service |
| ECR | 805778285334.dkr.ecr.us-east-1.amazonaws.com/sovereign-aiops-mcp-server |
| HITL API | https://788nqj8wtg.execute-api.us-east-1.amazonaws.com/prod |
| SNS Topic | arn:aws:sns:us-east-1:805778285334:sovereign-aiops-alarms |
| CloudTrail S3 | sovereign-aiops-cloudtrail-805778285334 |
| VPC | vpc-00724a90c9c7bde3c |

## cloudwatch-alarms module — important inputs

- `sns_topic_arn` — required. Create the SNS topic outside the module and pass the ARN.
- `mcp_server_lambda_arn` — required. EventBridge target always created (no count).
- `enable_alb_alarms`, `enable_ecs_alarms`, `enable_eks_alarms` — explicit booleans required
  for count to be static-at-plan-time. Do NOT derive from module outputs.

## Known Terraform issues resolved

- IAM duplicate tags: provider `default_tags` must NOT include keys that modules also set
  (e.g. `Project`, `ManagedBy`). Keep only keys that modules don't add (e.g. `environment`).
- CloudTrail KMS key must include `logs.<region>.amazonaws.com` in its key policy for
  CloudWatch Log Groups to use it.
