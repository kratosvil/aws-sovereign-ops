"""
Lambda HITL Notifier — API Gateway trigger only.

SNS delivers the incident notification directly to the operator via Email subscription.
This Lambda only handles the operator's APPROVE / REJECT decision:

  GET /approve?incident_id=&token=&approved_by=
  GET /reject?incident_id=&rejected_by=

It validates the params and forwards the decision to the MCP Server internal endpoint.
The operator sees a plain HTML confirmation page in their browser after clicking.
"""

import json
import logging
import os
import urllib.request
import urllib.parse

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MCP_SERVER_URL = os.environ["MCP_SERVER_URL"]   # internal ALB URL — no public access


def lambda_handler(event, context):
    # EventBridge event transformed by cloudwatch-alarms input_transformer:
    # { event_type, alarm_name, alarm_state, reason, timestamp, project }
    if event.get("event_type") == "alarm":
        return _forward_alarm_transformed(event)

    # Raw EventBridge event (no transformer) — fallback
    if event.get("source") == "aws.cloudwatch":
        return _forward_alarm_raw(event)

    # API Gateway events — APPROVE / REJECT from operator
    path   = event.get("path") or event.get("rawPath", "")
    params = event.get("queryStringParameters") or {}

    logger.info("hitl_request path=%s params=%s", path, list(params.keys()))

    if path.endswith("/approve"):
        return _approve(params)

    if path.endswith("/reject"):
        return _reject(params)

    return _page(404, "Not found", "Unknown path.")


# ------------------------------------------------------------------
# Handlers
# ------------------------------------------------------------------

def _extract_resource(dimensions: dict) -> tuple:
    """Derives resource_name and resource_type from CloudWatch alarm metric dimensions."""
    if not dimensions or not isinstance(dimensions, dict):
        return "", "generic"
    if "FunctionName" in dimensions:
        return dimensions["FunctionName"], "lambda"
    if "ServiceName" in dimensions and "ClusterName" in dimensions:
        return dimensions["ServiceName"], "ecs_service"
    if "DBInstanceIdentifier" in dimensions:
        return dimensions["DBInstanceIdentifier"], "rds"
    if "LoadBalancer" in dimensions:
        return dimensions["LoadBalancer"], "alb"
    return next(iter(dimensions.values()), ""), "generic"


def _forward_alarm_transformed(event: dict) -> dict:
    """Handles EventBridge event already flattened by input_transformer in cloudwatch-alarms module."""
    dimensions = event.get("dimensions") or {}
    resource_name, resource_type = _extract_resource(dimensions)
    # explicit fields in event take precedence over derived values
    resource_name = event.get("resource_name") or resource_name
    resource_type = event.get("resource_type") or resource_type

    payload = {
        "alarm_name":   event.get("alarm_name", "unknown"),
        "alarm_state":  event.get("alarm_state", "ALARM"),
        "reason":       event.get("reason", ""),
        "timestamp":    event.get("timestamp", ""),
        "project":      event.get("project", os.environ.get("PROJECT_NAME", "sovereign-aiops")),
        "resource_name": resource_name,
        "resource_type": resource_type,
    }
    logger.info("forwarding_alarm alarm=%s state=%s", payload["alarm_name"], payload["alarm_state"])
    result = _call_mcp("POST", "/alarm", payload)
    logger.info("alarm_forwarded result=%s", result)
    return {"statusCode": 200, "body": "forwarded"}


def _forward_alarm_raw(event: dict) -> dict:
    """Fallback: raw EventBridge CloudWatch Alarm State Change (no input_transformer)."""
    detail = event.get("detail", {})
    payload = {
        "alarm_name":   detail.get("alarmName", "unknown"),
        "alarm_state":  detail.get("state", {}).get("value", "ALARM"),
        "reason":       detail.get("state", {}).get("reason", ""),
        "timestamp":    event.get("time", ""),
        "project":      os.environ.get("PROJECT_NAME", "sovereign-aiops"),
        "resource_name": detail.get("resource_name", ""),
        "resource_type": detail.get("resource_type", "generic"),
    }
    logger.info("forwarding_alarm alarm=%s state=%s", payload["alarm_name"], payload["alarm_state"])
    result = _call_mcp("POST", "/alarm", payload)
    logger.info("alarm_forwarded result=%s", result)
    return {"statusCode": 200, "body": "forwarded"}


def _approve(params: dict) -> dict:
    incident_id = params.get("incident_id", "").strip()
    token       = params.get("token", "").strip()
    approved_by = params.get("approved_by", "operator").strip()

    if not incident_id or not token:
        return _page(400, "Bad request", "Missing incident_id or token.")

    result = _call_mcp("POST", "/approve", {
        "incident_id": incident_id,
        "token": token,
        "approved_by": approved_by,
    })

    if "error" in result:
        logger.error("approve_failed incident_id=%s error=%s", incident_id, result["error"])
        return _page(502, "Error", f"Could not reach MCP Server: {result['error']}")

    status = result.get("status", "unknown")
    if status == "rejected":
        return _page(403, "Token rejected", "The approval token is invalid or expired. Request a new approval.")

    logger.info("approved incident_id=%s approved_by=%s", incident_id, approved_by)
    # MCP Server returns 202 — execution runs in background
    return _page(200, "Approved", (
        f"Fix approved for incident <strong>{incident_id}</strong>. "
        "Execution started in background — check CloudWatch Logs for results."
    ))


def _reject(params: dict) -> dict:
    incident_id = params.get("incident_id", "").strip()
    rejected_by = params.get("rejected_by", "operator").strip()

    if not incident_id:
        return _page(400, "Bad request", "Missing incident_id.")

    result = _call_mcp("POST", "/reject", {
        "incident_id": incident_id,
        "rejected_by": rejected_by,
    })

    if "error" in result:
        logger.error("reject_failed incident_id=%s error=%s", incident_id, result["error"])
        return _page(502, "Error", f"Could not reach MCP Server: {result['error']}")

    logger.info("rejected incident_id=%s rejected_by=%s", incident_id, rejected_by)
    return _page(200, "Rejected", f"Fix rejected for incident <strong>{incident_id}</strong>. No changes applied.")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _call_mcp(method: str, path: str, payload: dict) -> dict:
    url  = MCP_SERVER_URL.rstrip("/") + path
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        logger.error("mcp_call_failed url=%s error=%s", url, exc)
        return {"error": str(exc)}


def _page(status: int, title: str, body: str) -> dict:
    """Returns a minimal HTML page shown to the operator in the browser after clicking."""
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>sovereign-aiops — {title}</title></head>
<body style="font-family:Arial,sans-serif;max-width:480px;margin:80px auto;text-align:center;">
  <h2>{title}</h2>
  <p>{body}</p>
  <p style="color:#888;font-size:12px;margin-top:40px;">sovereign-aiops HITL</p>
</body>
</html>"""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "text/html"},
        "body": html,
    }
