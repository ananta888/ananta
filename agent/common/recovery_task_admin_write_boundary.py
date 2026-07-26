"""Exact capability for Hub-owned Recovery task administration writes."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class RecoveryTaskAdminWriteAuthority:
    """Bind one administrative status write to its complete lineage."""

    task_id: str
    source_task_id: str
    goal_id: str
    action: str
    from_status: str
    to_status: str


_ACTIVE_AUTHORITY: ContextVar[
    RecoveryTaskAdminWriteAuthority | None
] = ContextVar(
    "ananta_recovery_task_admin_write_authority",
    default=None,
)


def _normalize(value: object) -> str:
    return str(value or "").strip()


def _authority(
    *,
    task_id: str,
    source_task_id: str,
    goal_id: str,
    action: str,
    from_status: str,
    to_status: str,
) -> RecoveryTaskAdminWriteAuthority:
    authority = RecoveryTaskAdminWriteAuthority(
        task_id=_normalize(task_id),
        source_task_id=_normalize(source_task_id),
        goal_id=_normalize(goal_id),
        action=_normalize(action).lower(),
        from_status=_normalize(from_status).lower(),
        to_status=_normalize(to_status).lower(),
    )
    if not all(
        (
            authority.task_id,
            authority.source_task_id,
            authority.goal_id,
            authority.action,
            authority.from_status,
            authority.to_status,
        )
    ):
        raise ValueError("recovery_task_admin_authority_invalid")
    return authority


@contextmanager
def authorize_recovery_task_admin_write(
    *,
    task_id: str,
    source_task_id: str,
    goal_id: str,
    action: str,
    from_status: str,
    to_status: str,
) -> Iterator[None]:
    """Authorize one exact Hub TaskAdmin status transition call chain."""

    authority = _authority(
        task_id=task_id,
        source_task_id=source_task_id,
        goal_id=goal_id,
        action=action,
        from_status=from_status,
        to_status=to_status,
    )
    token = _ACTIVE_AUTHORITY.set(authority)
    try:
        yield
    finally:
        _ACTIVE_AUTHORITY.reset(token)


def recovery_task_admin_write_authorized(
    *,
    task_id: str,
    source_task_id: str,
    goal_id: str,
    action: str,
    from_status: str,
    to_status: str,
) -> bool:
    try:
        expected = _authority(
            task_id=task_id,
            source_task_id=source_task_id,
            goal_id=goal_id,
            action=action,
            from_status=from_status,
            to_status=to_status,
        )
    except ValueError:
        return False
    return _ACTIVE_AUTHORITY.get() == expected


__all__ = [
    "RecoveryTaskAdminWriteAuthority",
    "authorize_recovery_task_admin_write",
    "recovery_task_admin_write_authorized",
]
