"""AuditEvent data model with serialization support."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class EventType(str, Enum):
    """Audit event type classification."""
    EXECUTION = "execution"
    DECISION = "decision"
    TOOL_CALL = "tool_call"
    MEMORY_ACCESS = "memory_access"
    MODEL_INVOCATION = "model_invocation"
    ERROR = "error"
    SECURITY = "security"
    SYSTEM = "system"


class EventStatus(str, Enum):
    """Audit event status."""
    SUCCESS = "success"
    FAILURE = "failure"
    PENDING = "pending"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass
class AuditEvent:
    """Represents a single audit log event."""

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    agent_id: str = ""
    agent_type: str = ""
    session_id: str = ""
    trace_id: str = ""
    parent_trace_id: Optional[str] = None
    event_type: str = EventType.EXECUTION.value
    action: str = ""
    target_type: str = ""
    target_id: str = ""
    input_summary: str = ""
    output_summary: str = ""
    decision: Optional[str] = None
    status: str = EventStatus.SUCCESS.value
    error: Optional[dict] = None
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        result = {}
        for k, v in asdict(self).items():
            if isinstance(v, Enum):
                result[k] = v.value
            else:
                result[k] = v
        return result

    @classmethod
    def from_dict(cls, data: dict) -> AuditEvent:
        """Deserialize from dictionary."""
        valid_fields = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)
