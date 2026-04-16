"""
Formats the incident notification for email (HTML) and Slack (Block Kit).
Generic — works for any AWS resource type and any action type.
"""

RISK_COLOR = {
    "low": "#2ecc71",
    "medium": "#f39c12",
    "high": "#e74c3c",
}

RISK_LABEL = {
    "low": "LOW",
    "medium": "MEDIUM",
    "high": "HIGH",
}


def _render_actions_html(actions: list) -> str:
    if not actions:
        return ""
    items = ""
    for a in actions:
        action_type = a.get("type", "unknown").upper()
        if a.get("command"):
            detail = f"<code>{a['command']}</code>"
        elif a.get("diff"):
            detail = f"<pre style='background:#f4f4f4;padding:8px;font-size:12px;'>{a['diff']}</pre>"
        elif a.get("description"):
            detail = f"<em>{a['description']}</em>"
        elif a.get("document"):
            detail = f"SSM document: <code>{a['document']}</code>"
        else:
            detail = str(a)
        items += f"<li><strong>[{action_type}]</strong> {detail}</li>"
    return f"<h3>Actions to execute</h3><ol>{items}</ol>"


def _render_actions_slack(actions: list) -> str:
    if not actions:
        return ""
    lines = []
    for a in actions:
        action_type = a.get("type", "unknown").upper()
        if a.get("command"):
            lines.append(f"• [{action_type}] `{a['command']}`")
        elif a.get("diff"):
            lines.append(f"• [{action_type}] terraform diff attached")
        elif a.get("description"):
            lines.append(f"• [{action_type}] {a['description']}")
        elif a.get("document"):
            lines.append(f"• [{action_type}] SSM: {a['document']}")
        else:
            lines.append(f"• [{action_type}]")
    return "\n*Actions to execute:*\n" + "\n".join(lines)


def email_html(incident: dict, approve_url: str, reject_url: str) -> str:
    risk = incident.get("risk", "unknown")
    color = RISK_COLOR.get(risk, "#95a5a6")
    actions_section = _render_actions_html(incident.get("actions", []))

    return f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;max-width:680px;margin:auto;padding:24px;">
  <div style="border-left:6px solid {color};padding-left:16px;margin-bottom:24px;">
    <h2 style="margin-top:0;">sovereign-aiops — Approval Required</h2>
    <p><strong>Incident ID:</strong> {incident.get("incident_id")}</p>
    <p><strong>Risk:</strong>
      <span style="color:{color};font-weight:bold;">{RISK_LABEL.get(risk, risk.upper())}</span>
    </p>
  </div>

  <h3>Root cause</h3>
  <p>{incident.get("root_cause")}</p>

  <h3>Proposed fix</h3>
  <p>{incident.get("fix_description")}</p>

  {actions_section}

  <h3>Expected outcome</h3>
  <p>{incident.get("expected_outcome")}</p>

  <p style="color:#888;font-size:12px;margin-top:24px;">
    Approval token expires in 15 minutes.
  </p>

  <div style="margin-top:32px;">
    <a href="{approve_url}"
       style="background:#2ecc71;color:#fff;padding:12px 28px;
              text-decoration:none;border-radius:4px;font-weight:bold;margin-right:12px;">
      APPROVE
    </a>
    <a href="{reject_url}"
       style="background:#e74c3c;color:#fff;padding:12px 28px;
              text-decoration:none;border-radius:4px;font-weight:bold;">
      REJECT
    </a>
  </div>
</body>
</html>""".strip()


def slack_blocks(incident: dict, approve_url: str, reject_url: str) -> dict:
    risk = incident.get("risk", "unknown")
    actions_text = _render_actions_slack(incident.get("actions", []))

    return {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "sovereign-aiops — Approval Required"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Incident ID:*\n{incident.get('incident_id')}"},
                    {"type": "mrkdwn", "text": f"*Risk:*\n{RISK_LABEL.get(risk, risk.upper())}"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Root cause:*\n{incident.get('root_cause')}\n\n"
                        f"*Proposed fix:*\n{incident.get('fix_description')}"
                        f"{actions_text}\n\n"
                        f"*Expected outcome:*\n{incident.get('expected_outcome')}"
                    ),
                },
            },
            {"type": "divider"},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "APPROVE"},
                        "style": "primary",
                        "url": approve_url,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "REJECT"},
                        "style": "danger",
                        "url": reject_url,
                    },
                ],
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "_Token expires in 15 minutes._"}],
            },
        ]
    }
