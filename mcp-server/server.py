"""
MCP Server — HTTP entrypoint.

Endpoints:
  POST /alarm    — receives EventBridge CloudWatch Alarm State Change event
  POST /approve  — receives human APPROVE decision with HITL token
  POST /reject   — receives human REJECT decision
  GET  /health   — health check for ECS load balancer
"""

import asyncio
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor

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
_executor = ThreadPoolExecutor(max_workers=4)

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
    Returns 202 immediately and processes the investigation in background.
    The operator receives an email with the APPROVE/REJECT link when done.
    """
    body = await request.json()
    logger.info("alarm_received detail_type=%s", body.get("detail-type"))

    event = _parse_alarm_event(body)

    loop = asyncio.get_event_loop()
    loop.run_in_executor(_executor, _process_alarm, event)

    return JSONResponse(
        content={"status": "accepted", "incident_id": event.alarm_name},
        status_code=202,
    )


def _process_alarm(event: AlarmEvent):
    """Runs investigation phase in a background thread."""
    try:
        result = orchestrator.handle_alarm(event)
        if result.get("status") == "awaiting_approval":
            _pending[result["incident_id"]] = {
                "event": event,
                "proposal": result["proposal"],
            }
    except Exception as exc:
        logger.error("alarm_processing_error alarm=%s error=%s", event.alarm_name, exc)


@app.post("/approve")
async def approve(request: Request):
    """
    Called when the operator approves the fix.
    Returns 202 immediately and runs execution in background (Bedrock takes >30s).
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

    loop = asyncio.get_event_loop()
    loop.run_in_executor(
        _executor,
        _process_approval,
        incident_id, token, approved_by, pending,
    )

    return JSONResponse(
        content={"status": "accepted", "incident_id": incident_id, "message": "Execution started"},
        status_code=202,
    )


def _process_approval(incident_id: str, token: str, approved_by: str, pending: dict):
    """Runs execution phase in a background thread."""
    try:
        result = orchestrator.handle_alarm(
            pending["event"],
            approval={"incident_id": incident_id, "token": token, "approved_by": approved_by},
            proposal=pending.get("proposal"),
        )
        if result.get("status") in ("execution_complete", "error"):
            _pending.pop(incident_id, None)
        logger.info("approval_processing_done incident_id=%s status=%s", incident_id, result.get("status"))
    except Exception as exc:
        logger.error("approval_processing_error incident_id=%s error=%s", incident_id, exc)


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
        resource_name=detail.get("resource_name", ""),
        resource_type=detail.get("resource_type", "generic"),
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("server:app", host="0.0.0.0", port=port, log_config=None)
