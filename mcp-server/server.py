"""
MCP Server — HTTP entrypoint.

Endpoints:
  POST /alarm    — receives EventBridge CloudWatch Alarm State Change event
  POST /approve  — receives human APPROVE decision with HITL token
  POST /reject   — receives human REJECT decision
  GET  /health   — health check for ECS load balancer
"""

import json
import logging
import os
import sys

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from models import AlarmEvent
from orchestrator import Orchestrator
from services.audit import AuditService

# Structured JSON logging — CloudWatch Logs picks this up
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
logger = logging.getLogger(__name__)

app = FastAPI(title="sovereign-aiops MCP Server", docs_url=None, redoc_url=None)

orchestrator = Orchestrator()
audit = AuditService()

# In-memory store for pending approvals (incident_id → proposal).
# In production, replace with DynamoDB or ElastiCache.
_pending: dict = {}


@app.get("/health")
def health():
    return {"status": "ok", "service": "sovereign-aiops-mcp-server"}


@app.post("/alarm")
async def receive_alarm(request: Request):
    """
    Triggered by EventBridge when a CloudWatch Alarm fires.
    Starts the investigation phase and sends the fix proposal to the operator.
    """
    body = await request.json()
    logger.info("alarm_received detail_type=%s", body.get("detail-type"))

    event = _parse_alarm_event(body)

    result = orchestrator.handle_alarm(event)

    if result.get("status") == "awaiting_approval":
        _pending[result["incident_id"]] = {
            "event": event,
            "proposal": result["proposal"],
        }

    return JSONResponse(content=result, status_code=200)


@app.post("/approve")
async def approve(request: Request):
    """
    Called when the operator approves the fix.
    Body: {"incident_id": "...", "token": "...", "approved_by": "..."}
    """
    body = await request.json()
    incident_id = body.get("incident_id")
    token = body.get("token")
    approved_by = body.get("approved_by", "unknown")

    if not incident_id or not token:
        raise HTTPException(status_code=400, detail="incident_id and token are required")

    pending = _pending.get(incident_id)
    if not pending:
        raise HTTPException(status_code=404, detail=f"No pending incident: {incident_id}")

    result = orchestrator.handle_alarm(
        pending["event"],
        approval={"incident_id": incident_id, "token": token, "approved_by": approved_by},
    )

    if result.get("status") == "execution_complete":
        _pending.pop(incident_id, None)

    return JSONResponse(content=result, status_code=200)


@app.post("/reject")
async def reject(request: Request):
    """
    Called when the operator rejects the fix.
    Body: {"incident_id": "...", "rejected_by": "..."}
    """
    body = await request.json()
    incident_id = body.get("incident_id")
    rejected_by = body.get("rejected_by", "unknown")

    if not incident_id:
        raise HTTPException(status_code=400, detail="incident_id is required")

    _pending.pop(incident_id, None)
    audit.approval_rejected(incident_id, rejected_by)

    logger.info("fix_rejected incident_id=%s rejected_by=%s", incident_id, rejected_by)
    return JSONResponse(content={"status": "rejected", "incident_id": incident_id})


def _parse_alarm_event(body: dict) -> AlarmEvent:
    """
    Parses an EventBridge CloudWatch Alarm State Change event.
    Also accepts a simplified payload for demo/testing.
    """
    detail = body.get("detail", body)
    return AlarmEvent(
        alarm_name=detail.get("alarmName", detail.get("alarm_name", "unknown")),
        alarm_state=detail.get("state", {}).get("value", detail.get("alarm_state", "ALARM")),
        reason=detail.get("state", {}).get("reason", detail.get("reason", "")),
        timestamp=detail.get("state", {}).get("timestamp", detail.get("timestamp", "")),
        project=os.environ.get("PROJECT_NAME", "sovereign-aiops"),
        raw_event=body,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("server:app", host="0.0.0.0", port=port, log_config=None)
