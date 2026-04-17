# Architecture — aws-sovereign-ops

## What it is

An autonomous incident response platform that runs entirely inside a private AWS VPC.
When any CloudWatch alarm fires — a pod crash, an RDS CPU spike, a Lambda error surge,
an ALB latency breach — the system investigates the failure using Amazon Bedrock,
proposes a fix, waits for a human to approve it, executes the approved fix, validates
the result, and rolls back automatically if validation fails.

No AI traffic ever leaves the VPC. Every action is immutably recorded in CloudTrail.
A human is always the final decision point before any change is applied.

Target industries: Fintech (SOC2 / PCI-DSS), Healthtech (HIPAA), SaaS B2B (SOC2).

---

## Architecture diagram

```
                        AWS Account (private VPC — zero-egress)
┌───────────────────────────────────────────────────────────────────────┐
│                                                                       │
│  ANY CloudWatch Alarm                                                 │
│  (OOMKilled · RDS CPU · Lambda errors · ALB latency · custom)        │
│         │                                                             │
│         ▼ EventBridge                                                 │
│  ┌─────────────────────────────────────────────────────────────┐      │
│  │  MCP Server (ECS Fargate — Python)                          │      │
│  │                                                             │      │
│  │  Phase 1 — Investigation (read-only)                        │      │
│  │  ┌───────────────┐    ┌──────────────────────────────────┐  │      │
│  │  │analyze_incident│──►│  Amazon Bedrock (via PrivateLink) │  │      │
│  │  │ CloudWatch logs│   │  Claude / Nova                   │  │      │
│  │  │ metrics, events│◄──│  reasons about root cause + fix  │  │      │
│  │  └───────────────┘    └──────────────────────────────────┘  │      │
│  │  ┌───────────────┐                                           │      │
│  │  │ propose_fix   │ ◄── Bedrock formalizes its own diagnosis  │      │
│  │  └───────────────┘                                           │      │
│  │         │                                                    │      │
│  │         ▼ SNS                                                │      │
│  └─────────────────────────────────────────────────────────────┘      │
│         │                                                             │
└─────────┼─────────────────────────────────────────────────────────────┘
          │ Email (SNS subscription)
          ▼
  OPERATOR receives:
  - Root cause (plain language)
  - Proposed fix + actions list
  - Risk level (low / medium / high)
  - Expected outcome
  - APPROVE link / REJECT link (API Gateway → Lambda → MCP Server)

          │ Operator clicks APPROVE (HMAC token, 15 min TTL)
          ▼
┌───────────────────────────────────────────────────────────────────────┐
│  MCP Server — Phase 2 — Execution                                     │
│                                                                       │
│  ┌─────────────────┐   validates token                                │
│  │ execute_approved │──────────────────► kubectl / terraform /        │
│  └─────────────────┘                    aws cli / ssm / manual        │
│         │                                                             │
│  ┌──────────────┐                                                     │
│  │ validate_fix  │──► CloudWatch metrics post-fix                     │
│  └──────────────┘                                                     │
│         │                                                             │
│         ├── fix confirmed ──► incident closed                         │
│         └── fix failed    ──► rollback() (requires new HITL token)   │
│                                                                       │
│  CloudTrail (S3 WORM + KMS) ◄── every action logged, immutable       │
└───────────────────────────────────────────────────────────────────────┘
```

---

## Components

| Component | Technology | Role |
|-----------|-----------|------|
| MCP Server | Python 3.11 · FastAPI · ECS Fargate | Orchestrates the full incident lifecycle |
| Bedrock PrivateLink | VPC Interface Endpoint | LLM inference without internet egress |
| Amazon Bedrock | Claude 3 Haiku (demo) / Sonnet (prod) | Reasons about failures and proposes fixes |
| CloudWatch Alarms | AWS CloudWatch + EventBridge | Detects any metric breach and triggers the flow |
| HITL Notifier | AWS Lambda + API Gateway | Delivers approval notification and captures operator decision |
| SNS Email subscription | Amazon SNS | Delivers plain-text incident report to operator |
| CloudTrail audit | S3 WORM bucket + KMS | Immutable record of every agent action |
| IAM least-privilege | AWS IAM | Agent has no IAM write, no billing, no root access |

---

## Incident response flow

```
1. ANY CloudWatch alarm fires
        │
        ▼
2. EventBridge routes event → POST /alarm (MCP Server)

3. MCP Server starts Bedrock agentic loop — investigation phase
   Bedrock calls analyze_incident():
     - pulls CloudWatch logs for the affected resource
     - pulls CloudWatch metrics (last 60 min)
     - pulls alarm state history
   Bedrock calls propose_fix():
     - records root cause, fix description, risk level
     - produces a typed actions list (see below)

4. MCP Server publishes to SNS → operator receives email with:
     - root cause in plain language
     - proposed fix
     - APPROVE / REJECT links (token embedded, 15 min expiry)

5. Operator clicks APPROVE
   → API Gateway → Lambda HITL Notifier → POST /approve (MCP Server)
   → HMAC token validated (incident_id + expiry + signature)

6. MCP Server starts Bedrock agentic loop — execution phase
   Bedrock calls execute_approved(token):
     - dispatches each action by type
     - kubectl · terraform · aws_cli · ssm · manual
   Bedrock calls validate_fix():
     - checks post-fix CloudWatch metrics
     - compares against per-resource-type health thresholds
   If validation fails:
     Bedrock calls rollback() — requires a second HITL token

7. Every step written to CloudTrail via structured JSON logs
   Format: {audit: true, event_type, incident_id, timestamp, ...}
```

---

## Design decisions

### Any AWS resource, any alarm type

The system is not limited to Kubernetes or Terraform failures.
It handles any CloudWatch alarm — EKS, ECS, RDS, Lambda, ALB, DynamoDB, ElastiCache, or custom metrics.

Bedrock (the LLM) is the reasoning engine. It determines the root cause and the appropriate
fix based on the logs and metrics it receives. The code does not hardcode failure types.

To add support for a new AWS service: add metric queries to `METRIC_QUERIES` and log group
templates to `LOG_GROUPS` in `mcp-server/tools/analyze_incident.py`. No other files change.

### Generic `actions` list

Instead of service-specific fields (`kubectl_commands`, `terraform_diff`), every fix is
expressed as a typed list of actions:

```json
[
  {"type": "kubectl",   "command": "kubectl patch deployment api-service ..."},
  {"type": "aws_cli",   "command": "aws rds reboot-db-instance --db-instance-identifier mydb"},
  {"type": "terraform", "diff": "resource \"aws_ecs_task_definition\" ..."},
  {"type": "ssm",       "document": "AWS-RunShellScript", "parameters": {"commands": [...]}},
  {"type": "manual",    "description": "Increase instance class via RDS console"}
]
```

`execute_approved` and `rollback` use a dispatcher pattern (`_run_<type>` / `_undo_<type>`).
Adding a new action type requires adding one method to each file — no schema changes.

### Bedrock via PrivateLink — not a dedicated model

Bedrock is a shared, multi-tenant AWS managed service. The model runs on AWS infrastructure.
What PrivateLink provides is a private network path — API calls travel over the AWS backbone,
never over the public internet. This satisfies SOC2 / HIPAA / PCI-DSS requirements that
sensitive operational data must not traverse public networks.

Data isolation guarantees: no persistence between requests, logical isolation per AWS account ID,
TLS encryption in transit.

### Human-in-the-Loop as the security contract

No action executes without a human-signed HMAC-SHA256 token (15 min TTL).
This is the commercial differentiator: enterprises buy this because the agent cannot
unilaterally modify production. The human is always the final decision point.

Read-only operations (analyze, propose, validate) run freely.
Write operations (execute, rollback) require a fresh token per incident.

### Circuit breaker

Two consecutive tool failures suspend the agent and escalate to the operator.
Prevents runaway automation in degraded environments.

---

## Running the demo

### Local demo (no AWS infra required)

```bash
# 1. Install MCP Server dependencies
make mcp-install

# 2. Set required environment variables
export AWS_REGION=us-east-1
export BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
export HITL_SNS_TOPIC=arn:aws:sns:us-east-1:805778285334:sovereign-aiops-alarms
export HITL_TOKEN_SECRET=sovereign-aiops-demo-secret
export API_BASE_URL=http://localhost:8080
export PROJECT_NAME=sovereign-aiops
export AWS_ACCOUNT_ID=805778285334

# 3. Start MCP Server
make mcp-run

# 4. Run demo (new terminal)
bash scripts/demo.sh              # interactive menu
bash scripts/demo.sh oomkilled    # EKS pod OOMKilled
bash scripts/demo.sh rds-cpu      # RDS CPU overload
bash scripts/demo.sh lambda-error # Lambda high error rate
bash scripts/demo.sh alb-latency  # ALB p99 latency breach
```

### Production (AWS infra deployed)

```bash
# HITL API endpoint
https://k4avvm6daf.execute-api.us-east-1.amazonaws.com/prod

# Approve an incident (operator flow)
curl -X POST https://k4avvm6daf.execute-api.us-east-1.amazonaws.com/prod/approve \
  -H "Content-Type: application/json" \
  -d '{"incident_id": "INCIDENT_ID", "token": "TOKEN", "approved_by": "operator-name"}'

# IMPORTANT: BEDROCK_MODEL_ID must use cross-region inference prefix
# Correct:   us.anthropic.claude-haiku-4-5-20251001-v1:0
# Incorrect: anthropic.claude-haiku-4-5-20251001-v1:0  ← ValidationException
```

---

## Estimated lab cost (48-hour demo)

| Resource | Cost |
|----------|------|
| VPC Interface Endpoints x3 (48h) | ~$1.75 |
| ECS Fargate — MCP Server (48h) | ~$0.50 |
| Bedrock Claude 3 Haiku — demo tokens | ~$0.50 |
| Lambda HITL + API Gateway | ~$0.00 (free tier) |
| CloudWatch + S3 + CloudTrail | ~$0.30 |
| **Total** | **< $5 USD** |

Run `make destroy-example` after the demo.
VPC Interface Endpoints charge per hour regardless of traffic.

---

## Repository structure

```
aws-sovereign-ops/
├── modules/
│   ├── bedrock-privatelink/   # VPC Interface Endpoints: Bedrock, CloudWatch, STS
│   ├── cloudtrail-audit/      # CloudTrail + S3 WORM + KMS
│   └── cloudwatch-alarms/     # Pre-built alarms + EventBridge routing
├── examples/
│   └── sovereign-aiops/       # Full Terraform stack — assembles all modules
├── mcp-server/
│   ├── server.py              # FastAPI: /alarm /approve /reject /health
│   ├── orchestrator.py        # Agentic loop, 2 phases, circuit breaker
│   ├── models.py              # AlarmEvent, FixProposal, ExecutionResult
│   ├── services/
│   │   ├── bedrock.py         # Bedrock converse API with tool_use
│   │   ├── hitl.py            # HMAC token + SNS plain-text notification
│   │   └── audit.py           # Structured JSON logs → CloudTrail
│   └── tools/
│       ├── analyze_incident.py  # Generic: eks_pod, ecs_service, rds, lambda, alb
│       ├── propose_fix.py       # Bedrock formalizes its own diagnosis
│       ├── execute_approved.py  # Dispatcher: kubectl, terraform, aws_cli, ssm, manual
│       ├── validate_fix.py      # Post-fix metrics, per-resource health thresholds
│       └── rollback.py          # Undo dispatcher, mirrors execute_approved
├── lambda/
│   └── hitl-notifier/         # Lambda + API Gateway + SNS Email subscription
├── scripts/
│   └── demo.sh                # Full remediation loop without infrastructure
└── docs/
    └── architecture.md        # This file
```
