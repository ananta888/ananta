"""CaseFlow Actions — generic action and reminder system for cases."""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ActionStatus(str, Enum):
    open = "open"
    in_progress = "in_progress"
    waiting = "waiting"
    done = "done"
    cancelled = "cancelled"


class CaseAction(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    case_id: str
    action_type: str
    title: str
    description: Optional[str] = None
    status: ActionStatus = ActionStatus.open
    due_at: Optional[datetime] = None
    priority: str = "medium"
    assigned_to: Optional[str] = None
    created_by: str = "system"
    completed_at: Optional[datetime] = None
    blocking: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ApprovalRequest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    case_id: str
    action_id: Optional[str] = None
    critical_action: str  # "send_application_email" | "delete_case" | etc.
    payload_hash: Optional[str] = None
    requested_by: str
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    rejected_by: Optional[str] = None
    rejected_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    status: str = "pending"  # "pending" | "approved" | "rejected"


CRITICAL_ACTIONS = [
    "send_application_email",
    "send_followup_email",
    "delete_case",
    "convert_discovery_result_to_case",
    "cloud_model_with_sensitive_data",
]

_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def next_action(actions: list[CaseAction]) -> Optional[CaseAction]:
    """Return the highest-priority open action.

    Sorting: blocking first, then by due_at (earliest first, None last), then priority.
    """
    open_actions = [a for a in actions if a.status == ActionStatus.open]
    if not open_actions:
        return None

    def sort_key(a: CaseAction):
        blocking_rank = 0 if a.blocking else 1
        due_rank = a.due_at.timestamp() if a.due_at else float("inf")
        priority_rank = _PRIORITY_ORDER.get(a.priority, 2)
        return (blocking_rank, due_rank, priority_rank)

    return sorted(open_actions, key=sort_key)[0]
