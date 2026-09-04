"""Infrastructure-free classification and external mutation guard policy."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

RecoveryTaskRole = Literal["source", "child"]

_TERMINAL_TASK_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "cancelled",
        "verification_failed",
        "skipped",
        "aborted",
        "timeout",
        "archived",
    }
)


def _value(task: Any, name: str) -> Any:
    if isinstance(task, dict):
        return task.get(name)
    return getattr(task, name, None)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _recovery_details(task: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for field in ("verification_status", "status_reason_details"):
        payload = _mapping(_value(task, field))
        for key in (
            "model_recovery",
            "model_recovery_strategy",
            "model_recovery_release",
            "recovery_dispatch_lease",
        ):
            if isinstance(payload.get(key), dict) and payload.get(key):
                merged[key] = dict(payload[key])
    return merged


def recovery_task_role(task: Any) -> RecoveryTaskRole | None:
    """Classify Hub-owned Recovery state without depending on API layers."""

    details = _recovery_details(task)
    is_child = bool(
        str(_value(task, "derivation_reason") or "").strip()
        == "goal_task_recovery"
        or details.get("model_recovery_release")
        or details.get("recovery_dispatch_lease")
    )
    if is_child:
        return "child"
    if details.get("model_recovery") or details.get(
        "model_recovery_strategy"
    ):
        return "source"
    return None


def is_active_recovery_task(task: Any, *, now: float | None = None) -> bool:
    role = recovery_task_role(task)
    if role is None:
        return False
    status = str(_value(task, "status") or "").strip().lower()
    lease = _mapping(_recovery_details(task).get("recovery_dispatch_lease"))
    lease_state = str(lease.get("state") or "").strip().lower()
    try:
        lease_expires_at = float(lease.get("expires_at") or 0.0)
    except (TypeError, ValueError):
        lease_expires_at = float("inf")
    lease_active = (
        lease_state in {"active", "worker_admitted"}
        and lease_expires_at > float(now or time.time())
    )
    return status not in _TERMINAL_TASK_STATUSES or lease_active


@dataclass
class RecoveryTaskMutationConflict(RuntimeError):
    """Structured conflict for an external mutation of Hub Recovery state."""

    reason_code: str
    task_id: str
    role: RecoveryTaskRole
    action: str
    source_task_id: str | None = None
    plan_id: str | None = None

    def __post_init__(self) -> None:
        RuntimeError.__init__(
            self,
            f"{self.reason_code}:{self.task_id}:{self.action}",
        )

    def as_data(self) -> dict[str, Any]:
        return {
            "reason_code": self.reason_code,
            "task_id": self.task_id,
            "recovery_role": self.role,
            "source_task_id": self.source_task_id,
            "plan_id": self.plan_id,
            "action": self.action,
            "http_status": 409,
        }


def ensure_external_recovery_mutation_allowed(
    task: Any,
    *,
    action: str,
) -> None:
    """Deny API/projection writes that bypass the Hub Recovery saga."""

    role = recovery_task_role(task)
    if role is None:
        return
    task_id = str(_value(task, "id") or "").strip()
    source_task_id = str(
        _value(task, "source_task_id") or ""
    ).strip() or (task_id if role == "source" else None)
    recovery = _mapping(_recovery_details(task).get("model_recovery"))
    plan_id = (
        str(_value(task, "plan_id") or "").strip()
        or str(recovery.get("plan_id") or "").strip()
        or None
    )
    raise RecoveryTaskMutationConflict(
        reason_code=(
            "recovery_child_mutation_requires_new_plan_approval"
            if role == "child"
            else "recovery_source_mutation_requires_hub_control"
        ),
        task_id=task_id,
        role=role,
        action=str(action or "external_mutation").strip().lower(),
        source_task_id=source_task_id,
        plan_id=plan_id,
    )
