"""
Agentic loop orchestrator.

Phases:
  1. Investigation  — Bedrock calls analyze_incident + propose_fix (read-only)
  2. HITL pause     — operator receives notification, approves or rejects
  3. Execution      — Bedrock calls execute_approved + validate_fix (+ rollback if needed)

Circuit breaker: 2 consecutive tool failures → suspend and escalate.
"""

import logging
import time
import uuid
from typing import Optional

from models import AlarmEvent
from services.audit import AuditService
from services.bedrock import BedrockService, INVESTIGATION_TOOLS, EXECUTION_TOOLS
from services.hitl import HITLService
from tools.analyze_incident import AnalyzeIncidentTool, SCHEMA as ANALYZE_SCHEMA
from tools.execute_approved import ExecuteApprovedTool, SCHEMA as EXECUTE_SCHEMA
from tools.propose_fix import ProposeFixTool, SCHEMA as PROPOSE_SCHEMA, get_current_proposal, clear_proposal
from tools.rollback import RollbackTool, SCHEMA as ROLLBACK_SCHEMA
from tools.validate_fix import ValidateFixTool, SCHEMA as VALIDATE_SCHEMA

logger = logging.getLogger(__name__)

def _build_system_prompt() -> str:
    import os
    account_id = os.environ.get("AWS_ACCOUNT_ID", "")
    region = os.environ.get("AWS_REGION", "us-east-1")
    project = os.environ.get("PROJECT_NAME", "sovereign-aiops")
    account_line = f"- AWS Account ID: {account_id}" if account_id else "- AWS Account ID: unknown (do not use ACCOUNT-ID as placeholder — omit ARNs that require it)"

    return f"""You are a sovereign-aiops incident response agent.

Environment:
- AWS Region: {region}
- Project: {project}
{account_line}

Your job:
1. Investigate infrastructure failures using analyze_incident.
2. Identify the root cause and call propose_fix with your diagnosis.
3. After human approval, execute the fix with execute_approved.
4. Validate the fix with validate_fix.
5. If validation fails, call rollback.

Rules:
- Always call analyze_incident before propose_fix.
- Never execute anything without a valid approval token.
- Never use placeholder values like ACCOUNT-ID in commands — use the real account ID from environment or omit the ARN.
- Be concise and precise in your reasoning.
- If you cannot determine a safe fix, set risk=high and explain why in fix_description.
"""

SYSTEM_PROMPT = _build_system_prompt()

MAX_LOOP_ITERATIONS = 10
CIRCUIT_BREAKER_THRESHOLD = 2
AGENTIC_LOOP_MAX_SECONDS = 300  # 5-minute wall-clock timeout per phase


class Orchestrator:
    def __init__(self):
        self.bedrock = BedrockService()
        self.hitl = HITLService()
        self.audit = AuditService()
        self.tools = {
            "analyze_incident": AnalyzeIncidentTool(),
            "propose_fix": ProposeFixTool(),
            "execute_approved": ExecuteApprovedTool(self.hitl),
            "validate_fix": ValidateFixTool(),
            "rollback": RollbackTool(self.hitl),
        }
        self.tool_schemas = {
            "analyze_incident": ANALYZE_SCHEMA,
            "propose_fix": PROPOSE_SCHEMA,
            "execute_approved": EXECUTE_SCHEMA,
            "validate_fix": VALIDATE_SCHEMA,
            "rollback": ROLLBACK_SCHEMA,
        }

    def handle_alarm(
        self,
        event: AlarmEvent,
        approval: Optional[dict] = None,
        proposal: Optional[dict] = None,
    ) -> dict:
        """
        Main entrypoint. Call with just the alarm event to run the investigation phase.
        Call again with approval={incident_id, token, approved_by} and proposal to run
        the execution phase. Passing proposal avoids relying on the module-level global.
        """
        incident_id = approval["incident_id"] if approval else str(uuid.uuid4())[:8]
        self.audit.incident_received(incident_id, event.alarm_name)

        if approval:
            return self._run_execution_phase(incident_id, event, approval, proposal)
        return self._run_investigation_phase(incident_id, event)

    # ------------------------------------------------------------------
    # Phase 1: Investigation
    # ------------------------------------------------------------------

    def _run_investigation_phase(self, incident_id: str, event: AlarmEvent) -> dict:
        clear_proposal()

        messages = [
            {
                "role": "user",
                "content": [{"text": (
                    f"Incident ID: {incident_id}\n"
                    f"Alarm: {event.alarm_name}\n"
                    f"State: {event.alarm_state}\n"
                    f"Reason: {event.reason}\n"
                    f"Timestamp: {event.timestamp}\n"
                    + (f"Resource name: {event.resource_name}\n" if event.resource_name else "")
                    + (f"Resource type: {event.resource_type}\n" if event.resource_type != "generic" else "")
                    + "\nInvestigate this incident and propose a fix. "
                    "Use the exact resource_name provided above when calling tools or generating AWS CLI commands."
                )}],
            }
        ]
        schemas = [self.tool_schemas[t] for t in INVESTIGATION_TOOLS]

        result = self._agentic_loop(incident_id, messages, schemas, INVESTIGATION_TOOLS)

        proposal = get_current_proposal()
        if not proposal:
            logger.warning("investigation_no_proposal incident_id=%s", incident_id)
            return {"status": "no_proposal", "incident_id": incident_id}

        self.audit.fix_proposed(incident_id, proposal["risk"], proposal["fix_description"])
        token = self.hitl.notify_operator(incident_id, proposal)
        self.audit.hitl_notified(incident_id)

        return {
            "status": "awaiting_approval",
            "incident_id": incident_id,
            "proposal": proposal,
            "approval_token": token,
            "bedrock_reasoning": result.get("final_text"),
        }

    # ------------------------------------------------------------------
    # Phase 2: Execution (called after operator approves)
    # ------------------------------------------------------------------

    def _run_execution_phase(
        self, incident_id: str, event: AlarmEvent, approval: dict, proposal: Optional[dict] = None
    ) -> dict:
        self.audit.approval_received(incident_id, approval.get("approved_by", "unknown"))

        # Prefer proposal passed explicitly; fall back to module-level global
        if not proposal:
            proposal = get_current_proposal()
        if not proposal:
            logger.error("execution_no_proposal incident_id=%s", incident_id)
            return {"status": "error", "reason": "No proposal found for this incident."}

        import json as _json
        actions_json = _json.dumps(proposal.get("actions", []))
        logger.info("execution_phase_start incident_id=%s actions=%s", incident_id, actions_json)

        alarm_name = event.alarm_name
        messages = [
            {
                "role": "user",
                "content": [{"text": (
                    f"Incident ID: {incident_id}\n"
                    f"Triggering alarm: {alarm_name}\n"
                    f"The operator has approved the fix. Approval token: {approval['token']}\n"
                    f"Approved by: {approval.get('approved_by', 'unknown')}\n\n"
                    f"Execute the approved fix actions listed below.\n"
                    f"Actions (JSON): {actions_json}\n\n"
                    f"Call execute_approved with the incident_id, token, and the full actions list. "
                    f"After executing, call validate_fix to confirm the fix worked. "
                    f"Pass triggering_alarm='{alarm_name}' to validate_fix so it excludes the "
                    "triggering alarm from the active-alarms check (it takes several minutes to "
                    "transition back to OK after a fix is applied). "
                    "If validation fails, call rollback with the appropriate undo_actions."
                )}],
            }
        ]
        schemas = [self.tool_schemas[t] for t in EXECUTION_TOOLS]

        result = self._agentic_loop(incident_id, messages, schemas, EXECUTION_TOOLS)

        return {
            "status": "execution_complete",
            "incident_id": incident_id,
            "bedrock_reasoning": result.get("final_text"),
            "actions": result.get("actions"),
        }

    # ------------------------------------------------------------------
    # Core agentic loop
    # ------------------------------------------------------------------

    def _agentic_loop(
        self, incident_id: str, messages: list, schemas: list, allowed_tools: list
    ) -> dict:
        consecutive_failures = 0
        actions = []
        loop_start = time.time()

        for iteration in range(MAX_LOOP_ITERATIONS):
            elapsed = time.time() - loop_start
            if elapsed > AGENTIC_LOOP_MAX_SECONDS:
                logger.error(
                    "agentic_loop_timeout incident_id=%s iteration=%d elapsed=%.1fs",
                    incident_id, iteration, elapsed,
                )
                return {
                    "final_text": f"Loop timeout after {elapsed:.0f}s — escalating to human.",
                    "actions": actions,
                }
            response = self.bedrock.converse(messages, SYSTEM_PROMPT, schemas)
            stop_reason = response["stopReason"]

            # Append Bedrock's message to the conversation
            messages.append(response["output"]["message"])

            if stop_reason == "end_turn":
                return {"final_text": self.bedrock.extract_text(response), "actions": actions}

            if stop_reason != "tool_use":
                logger.warning("unexpected_stop_reason reason=%s", stop_reason)
                break

            tool_calls = self.bedrock.extract_tool_calls(response)
            tool_results_content = []

            for call in tool_calls:
                name = call["name"]
                inputs = call.get("input", {})
                tool_use_id = call["toolUseId"]

                if name not in allowed_tools:
                    result = {"error": f"Tool {name} not allowed in this phase."}
                else:
                    result, ok = self._call_tool(name, inputs)
                    if not ok:
                        consecutive_failures += 1
                    else:
                        consecutive_failures = 0

                actions.append({"tool": name, "inputs": inputs, "result": result})

                tool_results_content.append(
                    {
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            "content": [{"json": result}],
                        }
                    }
                )

                if consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                    self.audit.circuit_breaker_triggered(incident_id, consecutive_failures)
                    logger.error(
                        "circuit_breaker incident_id=%s failures=%d",
                        incident_id, consecutive_failures,
                    )
                    return {
                        "final_text": "Circuit breaker triggered — escalating to human.",
                        "actions": actions,
                    }

            messages.append({"role": "user", "content": tool_results_content})

        return {"final_text": "Max iterations reached.", "actions": actions}

    def _call_tool(self, name: str, inputs: dict) -> tuple:
        """Executes a tool. Returns (result_dict, success_bool)."""
        try:
            tool = self.tools[name]
            result = tool.execute(**inputs)
            logger.info("tool_called name=%s success=True", name)
            return result, True
        except Exception as exc:
            logger.error("tool_failed name=%s error=%s", name, exc)
            return {"error": str(exc)}, False
