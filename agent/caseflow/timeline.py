"""CaseFlow Timeline — event-sourcing-light for case history."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


EVENT_TYPES = [
    "case_created",
    "status_changed",
    "artifact_added",
    "action_created",
    "action_completed",
    "agent_run_started",
    "agent_run_completed",
    "communication_added",
    "approval_requested",
    "approval_granted",
    "approval_rejected",
    "discovery_result_converted",
    "case_workflow_started",
]


class CaseEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    case_id: str
    event_type: str
    actor_type: str = "system"  # "user" | "agent" | "system"
    actor_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    title: str
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: Optional[str] = None
    artifact_id: Optional[str] = None


# In-memory event store (used when no DB session is provided)
_events: list[CaseEvent] = []


def append_event(
    case_id: str,
    event: CaseEvent,
    db_session: Any = None,
) -> CaseEvent:
    """Append a case event. Stores in DB if session provided, else in-memory."""
    if db_session is not None:
        try:
            from agent.db_models.caseflow import CaseEventDB
            db_event = CaseEventDB(
                id=event.id,
                case_id=event.case_id,
                event_type=event.event_type,
                actor_type=event.actor_type,
                actor_id=event.actor_id,
                created_at=event.created_at,
                title=event.title,
                payload_json=__import__("json").dumps(event.payload),
                trace_id=event.trace_id,
                artifact_id=event.artifact_id,
            )
            db_session.add(db_event)
            db_session.commit()
        except Exception:
            pass
    else:
        _events.append(event)
    return event


def get_events_for_case(case_id: str) -> list[CaseEvent]:
    """Get in-memory events for a case, ordered by created_at."""
    return sorted(
        [e for e in _events if e.case_id == case_id],
        key=lambda e: (e.created_at, e.id),
    )


def clear_events() -> None:
    """Clear in-memory store. Used in tests."""
    _events.clear()
