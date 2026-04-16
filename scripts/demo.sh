#!/usr/bin/env bash
# =============================================================
# sovereign-aiops — demo script
# Simulates a real AWS alarm and runs the full remediation loop
# without deploying any infrastructure.
#
# Usage:
#   bash scripts/demo.sh                 # interactive menu
#   bash scripts/demo.sh oomkilled       # EKS pod OOMKilled
#   bash scripts/demo.sh rds-cpu         # RDS CPU overload
#   bash scripts/demo.sh lambda-error    # Lambda high error rate
#   bash scripts/demo.sh alb-latency     # ALB p99 latency breach
# =============================================================

set -euo pipefail

MCP_URL="${MCP_URL:-http://localhost:8080}"
PROJECT="${PROJECT_NAME:-sovereign-aiops}"

# ── colors ────────────────────────────────────────────────────
BOLD="\033[1m"
DIM="\033[2m"
RED="\033[31m"
GREEN="\033[32m"
YELLOW="\033[33m"
CYAN="\033[36m"
RESET="\033[0m"

# ── helpers ───────────────────────────────────────────────────
info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()      { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
err()     { echo -e "${RED}[ERROR]${RESET} $*"; exit 1; }
section() { echo -e "\n${BOLD}── $* ──────────────────────────────────────${RESET}"; }

# ── dependency check ──────────────────────────────────────────
check_deps() {
  for cmd in curl python3; do
    command -v "$cmd" &>/dev/null || err "Required: $cmd not found."
  done

  # jq is optional — fallback to python json.tool
  if command -v jq &>/dev/null; then
    JSON_FMT="jq ."
  else
    JSON_FMT="python3 -m json.tool"
    warn "jq not found — using python3 json.tool for formatting"
  fi
}

# ── MCP Server health check ───────────────────────────────────
wait_for_server() {
  info "Checking MCP Server at ${MCP_URL} ..."
  for i in $(seq 1 15); do
    if curl -sf "${MCP_URL}/health" &>/dev/null; then
      ok "MCP Server is up."
      return 0
    fi
    echo -n "."
    sleep 2
  done
  echo ""
  err "MCP Server not responding at ${MCP_URL}. Start it with: make mcp-run"
}

# ── alarm payloads ────────────────────────────────────────────
payload_oomkilled() {
  cat <<EOF
{
  "detail-type": "CloudWatch Alarm State Change",
  "source": "aws.cloudwatch",
  "detail": {
    "alarmName": "${PROJECT}-oomkilled",
    "state": {
      "value": "ALARM",
      "reason": "Threshold Crossed: pod api-service restarted due to OOMKilled (memory limit exceeded)",
      "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    },
    "configuration": {
      "description": "EKS pod terminated by OOM killer"
    }
  },
  "resource_name": "api-service",
  "resource_type": "eks_pod"
}
EOF
}

payload_rds_cpu() {
  cat <<EOF
{
  "detail-type": "CloudWatch Alarm State Change",
  "source": "aws.cloudwatch",
  "detail": {
    "alarmName": "${PROJECT}-rds-cpu-high",
    "state": {
      "value": "ALARM",
      "reason": "Threshold Crossed: CPUUtilization 94.3% > 85% for 10 minutes",
      "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    },
    "configuration": {
      "description": "RDS instance CPU overload"
    }
  },
  "resource_name": "sovereign-aiops-postgres",
  "resource_type": "rds"
}
EOF
}

payload_lambda_error() {
  cat <<EOF
{
  "detail-type": "CloudWatch Alarm State Change",
  "source": "aws.cloudwatch",
  "detail": {
    "alarmName": "${PROJECT}-lambda-errors",
    "state": {
      "value": "ALARM",
      "reason": "Threshold Crossed: Errors 47 > 5 in last 5 minutes",
      "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    },
    "configuration": {
      "description": "Lambda function high error rate"
    }
  },
  "resource_name": "sovereign-aiops-processor",
  "resource_type": "lambda"
}
EOF
}

payload_alb_latency() {
  cat <<EOF
{
  "detail-type": "CloudWatch Alarm State Change",
  "source": "aws.cloudwatch",
  "detail": {
    "alarmName": "${PROJECT}-high-latency",
    "state": {
      "value": "ALARM",
      "reason": "Threshold Crossed: TargetResponseTime p99 3.8s > 2s for 5 minutes",
      "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    },
    "configuration": {
      "description": "ALB p99 latency breach"
    }
  },
  "resource_name": "app/sovereign-aiops-alb/abc123",
  "resource_type": "alb"
}
EOF
}

# ── scenario menu ─────────────────────────────────────────────
select_scenario() {
  echo -e "\n${BOLD}Select incident scenario:${RESET}"
  echo "  1) EKS pod OOMKilled"
  echo "  2) RDS CPU overload (94%)"
  echo "  3) Lambda high error rate"
  echo "  4) ALB p99 latency breach"
  echo ""
  read -rp "Choice [1-4]: " choice
  case "$choice" in
    1) SCENARIO="oomkilled" ;;
    2) SCENARIO="rds-cpu" ;;
    3) SCENARIO="lambda-error" ;;
    4) SCENARIO="alb-latency" ;;
    *) err "Invalid choice: $choice" ;;
  esac
}

get_payload() {
  case "$1" in
    oomkilled)    payload_oomkilled ;;
    rds-cpu)      payload_rds_cpu ;;
    lambda-error) payload_lambda_error ;;
    alb-latency)  payload_alb_latency ;;
    *)            err "Unknown scenario: $1. Options: oomkilled, rds-cpu, lambda-error, alb-latency" ;;
  esac
}

# ── phase 1: trigger alarm ────────────────────────────────────
trigger_alarm() {
  local payload="$1"
  section "Phase 1 — Triggering alarm"
  info "POST ${MCP_URL}/alarm"

  RESPONSE=$(curl -sf -X POST "${MCP_URL}/alarm" \
    -H "Content-Type: application/json" \
    -d "$payload") || err "Failed to POST /alarm. Is MCP Server running?"

  echo "$RESPONSE" | $JSON_FMT

  STATUS=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))")
  INCIDENT_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('incident_id',''))")

  if [[ "$STATUS" != "awaiting_approval" ]]; then
    err "Unexpected status: $STATUS. Expected: awaiting_approval"
  fi

  ok "Incident ID: ${BOLD}${INCIDENT_ID}${RESET}"
}

# ── phase 2: show proposal and ask operator ───────────────────
operator_decision() {
  section "Phase 2 — Operator decision"

  PROPOSAL=$(echo "$RESPONSE" | python3 -c "
import sys, json
d = json.load(sys.stdin)
p = d.get('proposal', {})
print('Root cause  :', p.get('root_cause', 'N/A'))
print('Fix         :', p.get('fix_description', 'N/A'))
print('Risk        :', p.get('risk', 'N/A').upper())
print('Outcome     :', p.get('expected_outcome', 'N/A'))
actions = p.get('actions', [])
if actions:
    print('Actions     :')
    for i, a in enumerate(actions, 1):
        t = a.get('type','?').upper()
        detail = a.get('command') or a.get('description') or a.get('document') or ''
        print(f'  {i}. [{t}] {detail}')
")
  echo -e "$PROPOSAL"

  echo ""
  read -rp "$(echo -e "${BOLD}Approve this fix? [s/n]:${RESET} ")" decision
  DECISION="$decision"
}

# ── phase 3: approve ─────────────────────────────────────────
approve_fix() {
  section "Phase 3 — Executing approved fix"

  # Extract token from the response (returned by MCP Server for demo mode)
  # In production the operator gets the token via SNS email
  TOKEN=$(echo "$RESPONSE" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('approval_token', ''))
" 2>/dev/null || echo "")

  if [[ -z "$TOKEN" ]]; then
    read -rp "Token not found in response. Enter approval token from email: " TOKEN
  else
    echo -e "${DIM}Token extracted from response (demo mode).${RESET}"
  fi

  read -rp "Approved by (your name): " approved_by
  approved_by="${approved_by:-operator}"

  info "POST ${MCP_URL}/approve"

  EXEC_RESPONSE=$(curl -sf -X POST "${MCP_URL}/approve" \
    -H "Content-Type: application/json" \
    -d "{\"incident_id\": \"${INCIDENT_ID}\", \"token\": \"${TOKEN}\", \"approved_by\": \"${approved_by}\"}") \
    || err "Failed to POST /approve."

  echo "$EXEC_RESPONSE" | $JSON_FMT

  EXEC_STATUS=$(echo "$EXEC_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))")
  ok "Execution status: ${BOLD}${EXEC_STATUS}${RESET}"
}

# ── phase 3: reject ──────────────────────────────────────────
reject_fix() {
  section "Phase 3 — Rejecting fix"

  read -rp "Rejected by (your name): " rejected_by
  rejected_by="${rejected_by:-operator}"

  curl -sf -X POST "${MCP_URL}/reject" \
    -H "Content-Type: application/json" \
    -d "{\"incident_id\": \"${INCIDENT_ID}\", \"rejected_by\": \"${rejected_by}\"}" | $JSON_FMT

  warn "Fix rejected. No changes applied. Incident ${INCIDENT_ID} closed."
}

# ── summary ───────────────────────────────────────────────────
print_summary() {
  section "Demo complete"
  echo -e "  Incident ID : ${BOLD}${INCIDENT_ID}${RESET}"
  echo -e "  Scenario    : ${BOLD}${SCENARIO}${RESET}"
  echo -e "  Decision    : ${BOLD}${DECISION}${RESET}"
  echo -e "  MCP Server  : ${MCP_URL}"
  echo -e "\n${DIM}CloudTrail audit log written for every action.${RESET}"
}

# ── main ──────────────────────────────────────────────────────
main() {
  echo -e "${BOLD}"
  echo "  sovereign-aiops — Autonomous Incident Response Demo"
  echo "  Zero-egress · HITL · CloudTrail audit"
  echo -e "${RESET}"

  check_deps
  wait_for_server

  # Determine scenario
  SCENARIO="${1:-}"
  if [[ -z "$SCENARIO" ]]; then
    select_scenario
  fi

  PAYLOAD=$(get_payload "$SCENARIO")

  trigger_alarm "$PAYLOAD"
  operator_decision

  if [[ "$DECISION" =~ ^[sS]$ ]]; then
    approve_fix
  else
    reject_fix
  fi

  print_summary
}

main "$@"
