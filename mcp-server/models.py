from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class FixRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class AlarmEvent:
    alarm_name: str
    alarm_state: str
    reason: str
    timestamp: str
    project: str
    raw_event: dict


@dataclass
class IncidentContext:
    incident_id: str
    alarm: AlarmEvent
    resource_name: str
    logs: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    recent_alarm_events: list = field(default_factory=list)
    collected_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class FixProposal:
    """
    Generic fix proposal — works for any AWS resource type.

    actions is a list of steps Bedrock determined are needed. Each step has a 'type':
      {"type": "kubectl",   "command": "kubectl patch deployment api -p ..."}
      {"type": "terraform", "diff": "resource aws_ecs_task_definition ..."}
      {"type": "aws_cli",   "command": "aws rds reboot-db-instance --db-instance-identifier mydb"}
      {"type": "ssm",       "document": "AWS-RunShellScript", "parameters": {"commands": ["..."]}}
      {"type": "manual",    "description": "Increase instance class via RDS console — cannot automate"}
    """
    incident_id: str
    root_cause: str
    fix_description: str
    risk: FixRisk
    expected_outcome: str
    actions: list = field(default_factory=list)
    proposed_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ExecutionResult:
    incident_id: str
    actions_taken: list
    success: bool
    executed_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ValidationResult:
    incident_id: str
    fix_confirmed: bool
    metrics_post_fix: dict
    bedrock_assessment: str
    validated_at: datetime = field(default_factory=datetime.utcnow)
