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
    crash_history: list = field(default_factory=list)
    collected_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class FixProposal:
    incident_id: str
    root_cause: str
    fix_description: str
    risk: FixRisk
    expected_outcome: str
    kubectl_commands: list = field(default_factory=list)
    terraform_diff: Optional[str] = None
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
