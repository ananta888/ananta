"""Exact Hub capability for cancelling one Recovery child."""

from __future__ import annotations

import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

RECOVERY_CHILD_CANCELLATION_SCHEMA = (
    "ananta.recovery_child_cancellation.v1"
)
RECOVERY_CHILD_CANCELLATION_FIELDS = frozenset(
    {
        "schema",
        "task_id",
        "source_task_id",
        "goal_id",
        "plan_id",
        "previous_status",
        "target_status",
        "reason_code",
        "cancelled_at",
    }
)


@dataclass(frozen=True)
class RecoveryChildCancellationWriteAuthority:
    task_id: str
    source_task_id: str
    goal_id: str
    plan_id: str
    previous_status: str
    target_status: str
    reason_code: str
    cancelled_at: float


_ACTIVE_AUTHORITY: ContextVar[
    RecoveryChildCancellationWriteAuthority | None
] = ContextVar(
    "ananta_recovery_child_cancellation_write_authority",
    default=None,
)


def _required_identifier(value: Any) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized.encode("utf-8")) > 256
    ):
        raise ValueError(
            "recovery_child_cancellation_authority_invalid"
        )
    return normalized


def _optional_identifier(value: Any) -> str:
    normalized = str(value or "").strip()
    if len(normalized.encode("utf-8")) > 256:
        raise ValueError(
            "recovery_child_cancellation_authority_invalid"
        )
    return normalized


def _authority(
    *,
    task_id: str,
    marker: Any,
) -> RecoveryChildCancellationWriteAuthority:
    if not isinstance(marker, Mapping):
        raise ValueError(
            "recovery_child_cancellation_authority_invalid"
        )
    value = dict(marker)
    if (
        set(value) != RECOVERY_CHILD_CANCELLATION_FIELDS
        or value.get("schema")
        != RECOVERY_CHILD_CANCELLATION_SCHEMA
    ):
        raise ValueError(
            "recovery_child_cancellation_authority_invalid"
        )
    try:
        cancelled_at = float(value.get("cancelled_at"))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "recovery_child_cancellation_authority_invalid"
        ) from exc
    normalized_task_id = _required_identifier(task_id)
    authority = RecoveryChildCancellationWriteAuthority(
        task_id=normalized_task_id,
        source_task_id=_required_identifier(
            value.get("source_task_id")
        ),
        goal_id=_required_identifier(value.get("goal_id")),
        plan_id=_optional_identifier(value.get("plan_id")),
        previous_status=str(
            value.get("previous_status") or ""
        )
        .strip()
        .lower(),
        target_status=str(value.get("target_status") or "")
        .strip()
        .lower(),
        reason_code=str(value.get("reason_code") or "").strip(),
        cancelled_at=cancelled_at,
    )
    if (
        str(value.get("task_id") or "").strip()
        != normalized_task_id
        or not authority.previous_status
        or authority.previous_status
        in {
            "completed",
            "failed",
            "cancelled",
            "verification_failed",
            "skipped",
            "aborted",
            "timeout",
            "archived",
        }
        or authority.target_status != "cancelled"
        or not authority.reason_code
        or len(authority.reason_code.encode("utf-8")) > 160
        or not math.isfinite(authority.cancelled_at)
        or authority.cancelled_at <= 0.0
    ):
        raise ValueError(
            "recovery_child_cancellation_authority_invalid"
        )
    return authority


@contextmanager
def authorize_recovery_child_cancellation_write(
    *,
    task_id: str,
    marker: Any,
) -> Iterator[None]:
    authority = _authority(task_id=task_id, marker=marker)
    token = _ACTIVE_AUTHORITY.set(authority)
    try:
        yield
    finally:
        _ACTIVE_AUTHORITY.reset(token)


def recovery_child_cancellation_write_authorized(
    *,
    task_id: str,
    marker: Any,
) -> bool:
    try:
        expected = _authority(task_id=task_id, marker=marker)
    except (TypeError, ValueError):
        return False
    return _ACTIVE_AUTHORITY.get() == expected


__all__ = [
    "RECOVERY_CHILD_CANCELLATION_FIELDS",
    "RECOVERY_CHILD_CANCELLATION_SCHEMA",
    "RecoveryChildCancellationWriteAuthority",
    "authorize_recovery_child_cancellation_write",
    "recovery_child_cancellation_write_authorized",
]
