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
        Publishes the fix proposal to SNS so the operator receives it via
        email/Slack. The notification includes the pre-generated approval token.
        """
        token = self.generate_token(incident_id)
        message = {
            "incident_id": incident_id,
            "root_cause": proposal.get("root_cause"),
            "fix_description": proposal.get("fix_description"),
            "risk": proposal.get("risk"),
            "expected_outcome": proposal.get("expected_outcome"),
            "kubectl_commands": proposal.get("kubectl_commands", []),
            "terraform_diff": proposal.get("terraform_diff"),
            "approval_token": token,
            "token_expires_in": "15 minutes",
            "instructions": (
                "To approve, call POST /approve with "
                '{"incident_id": "<id>", "token": "<token>", "approved_by": "<name>"}'
            ),
        }
        self.sns.publish(
            TopicArn=self.sns_topic_arn,
            Subject=f"[sovereign-aiops] APPROVAL REQUIRED — {incident_id}",
            Message=json.dumps(message, indent=2),
        )
        logger.info("hitl_notification_sent incident_id=%s", incident_id)

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
