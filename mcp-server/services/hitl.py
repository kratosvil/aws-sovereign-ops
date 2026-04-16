import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import boto3

logger = logging.getLogger(__name__)

TOKEN_TTL_SECONDS = 15 * 60  # 15 minutes


class HITLService:
    def __init__(self):
        self.sns = boto3.client("sns", region_name=os.environ["AWS_REGION"])
        self.sns_topic_arn = os.environ["HITL_SNS_TOPIC"]
        self._secret = os.environ.get("HITL_TOKEN_SECRET", "change-me-in-prod")

    def notify_operator(self, incident_id: str, proposal: dict) -> None:
        """
        Publishes the fix proposal to SNS.
        SNS Email subscription delivers the message directly to the operator.
        Message is formatted as readable plain text — no SES or HTML required.
        """
        token = self.generate_token(incident_id)
        api_base = os.environ.get("API_BASE_URL", "").rstrip("/")

        approve_url = f"{api_base}/approve?incident_id={incident_id}&token={token}&approved_by=operator"
        reject_url  = f"{api_base}/reject?incident_id={incident_id}&rejected_by=operator"

        actions_text = self._format_actions(proposal.get("actions", []))

        message = (
            f"sovereign-aiops — APPROVAL REQUIRED\n"
            f"{'=' * 50}\n\n"
            f"Incident ID : {incident_id}\n"
            f"Risk        : {proposal.get('risk', 'unknown').upper()}\n\n"
            f"ROOT CAUSE\n{proposal.get('root_cause')}\n\n"
            f"PROPOSED FIX\n{proposal.get('fix_description')}\n\n"
            f"{actions_text}"
            f"EXPECTED OUTCOME\n{proposal.get('expected_outcome')}\n\n"
            f"{'=' * 50}\n"
            f"Token expires in 15 minutes.\n\n"
            f"APPROVE: {approve_url}\n\n"
            f"REJECT : {reject_url}\n"
        )

        self.sns.publish(
            TopicArn=self.sns_topic_arn,
            Subject=f"[sovereign-aiops] APPROVAL REQUIRED — {incident_id} [{proposal.get('risk','?').upper()}]",
            Message=message,
        )
        logger.info("hitl_notification_sent incident_id=%s", incident_id)

    def _format_actions(self, actions: list) -> str:
        if not actions:
            return ""
        lines = ["ACTIONS TO EXECUTE"]
        for i, a in enumerate(actions, 1):
            action_type = a.get("type", "unknown").upper()
            if a.get("command"):
                lines.append(f"  {i}. [{action_type}] {a['command']}")
            elif a.get("description"):
                lines.append(f"  {i}. [{action_type}] {a['description']}")
            elif a.get("document"):
                lines.append(f"  {i}. [{action_type}] SSM: {a['document']}")
            elif a.get("diff"):
                lines.append(f"  {i}. [{action_type}] terraform diff (see logs)")
            else:
                lines.append(f"  {i}. [{action_type}]")
        return "\n".join(lines) + "\n\n"

    def generate_token(self, incident_id: str) -> str:
        """
        Generates a signed HMAC token: {incident_id}:{expiry_unix}:{signature}
        The token is sent to the operator inside the approval notification.
        """
        expiry = int((datetime.now(timezone.utc) + timedelta(seconds=TOKEN_TTL_SECONDS)).timestamp())
        payload = f"{incident_id}:{expiry}"
        sig = hmac.new(self._secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}:{sig}"

    def validate_token(self, token: str, incident_id: str) -> bool:
        """
        Returns True only if the token is valid, not expired, and matches the incident.
        Uses constant-time comparison to prevent timing attacks.
        """
        try:
            parts = token.split(":")
            if len(parts) != 3:
                return False
            tid, expiry_str, sig = parts
            if tid != incident_id:
                return False
            if int(expiry_str) < time.time():
                logger.warning("hitl_token_expired incident_id=%s", incident_id)
                return False
            expected = hmac.new(
                self._secret.encode(),
                f"{tid}:{expiry_str}".encode(),
                hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(sig, expected)
        except Exception:
            return False
