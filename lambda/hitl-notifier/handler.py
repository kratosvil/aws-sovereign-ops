"""
Lambda HITL Notifier — two responsibilities:

1. SNS trigger (source: aws:sns)
   Receives the incident notification from the MCP Server, formats it,
   and delivers it to the operator via email and/or Slack.

2. API Gateway trigger (source: API GW — GET /approve or /reject)
   Receives the operator decision (click from email/Slack),
   validates basic params, and forwards to the MCP Server /approve or /reject endpoint.
"""

import json
import logging
import os
import urllib.request
import urllib.parse

from formatter import email_html, slack_blocks

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MCP_SERVER_URL = os.environ["MCP_SERVER_URL"]        # internal ALB URL of the MCP Server
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")   # optional
API_BASE_URL = os.environ["API_BASE_URL"]            # public URL of this API Gateway


def lambda_handler(event, context):
    # Determine trigger source
    if "Records" in event and event["Records"][0].get("EventSource") == "aws:sns":
        return _handle_sns(event)

    # API Gateway — operator clicking APPROVE or REJECT
    if "queryStringParameters" in event:
        return _handle_api_gw(event)

    logger.warning("unrecognized_event_source keys=%s", list(event.keys()))
    return {"statusCode": 400, "body": "Unrecognized event source"}


# ------------------------------------------------------------------
# Trigger 1: SNS — deliver notification to operator
# ------------------------------------------------------------------

def _handle_sns(event: dict) -> dict:
    for record in event["Records"]:
        raw = record["Sns"]["Message"]
        try:
            incident = json.loads(raw)
        except json.JSONDecodeError:
            logger.error("sns_message_not_json message=%s", raw[:200])
            continue

        incident_id = incident.get("incident_id", "unknown")
        token = incident.get("approval_token", "")

        approve_url = (
            f"{API_BASE_URL}/approve?"
            f"incident_id={urllib.parse.quote(incident_id)}&"
            f"token={urllib.parse.quote(token)}&"
            f"approved_by=operator"
        )
        reject_url = (
            f"{API_BASE_URL}/reject?"
            f"incident_id={urllib.parse.quote(incident_id)}&"
            f"rejected_by=operator"
        )

        logger.info("hitl_notification incident_id=%s risk=%s", incident_id, incident.get("risk"))

        if SLACK_WEBHOOK_URL:
            _send_slack(incident, approve_url, reject_url)

        # Email is handled by the SNS email subscription configured in Terraform.
        # The SNS message already contains the full formatted text — no extra call needed.
        # Slack is an additional channel if SLACK_WEBHOOK_URL is set.

    return {"statusCode": 200}


# ------------------------------------------------------------------
# Trigger 2: API Gateway — forward operator decision to MCP Server
# ------------------------------------------------------------------

def _handle_api_gw(event: dict) -> dict:
    path = event.get("path", event.get("rawPath", ""))
    params = event.get("queryStringParameters") or {}

    incident_id = params.get("incident_id", "")
    if not incident_id:
        return _response(400, {"error": "incident_id is required"})

    if path.endswith("/approve"):
        token = params.get("token", "")
        approved_by = params.get("approved_by", "operator")
        if not token:
            return _response(400, {"error": "token is required"})

        result = _call_mcp("POST", "/approve", {
            "incident_id": incident_id,
            "token": token,
            "approved_by": approved_by,
        })
        logger.info("approval_forwarded incident_id=%s status=%s", incident_id, result.get("status"))
        return _response(200, {"message": "Approval submitted.", "result": result})

    if path.endswith("/reject"):
        rejected_by = params.get("rejected_by", "operator")
        result = _call_mcp("POST", "/reject", {
            "incident_id": incident_id,
            "rejected_by": rejected_by,
        })
        logger.info("rejection_forwarded incident_id=%s", incident_id)
        return _response(200, {"message": "Fix rejected.", "result": result})

    return _response(404, {"error": f"Unknown path: {path}"})


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _call_mcp(method: str, path: str, payload: dict) -> dict:
    url = MCP_SERVER_URL.rstrip("/") + path
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        logger.error("mcp_call_failed url=%s error=%s", url, exc)
        return {"error": str(exc)}


def _send_slack(incident: dict, approve_url: str, reject_url: str) -> None:
    payload = slack_blocks(incident, approve_url, reject_url)
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        SLACK_WEBHOOK_URL,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        logger.info("slack_notification_sent incident_id=%s", incident.get("incident_id"))
    except Exception as exc:
        logger.warning("slack_notification_failed error=%s", exc)


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
