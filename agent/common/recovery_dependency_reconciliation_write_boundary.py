"""Exact Hub capability for one Recovery dependency terminalization."""

from __future__ import annotations

import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

RECOVERY_DEPENDENCY_RECONCILIATION_SCHEMA = (
    "ananta.recovery_dependency_reconciliation.v1"
)
RECOVERY_DEPENDENCY_RECONCILIATION_FIELDS = frozenset(
    {
        "schema",
        "task_id",
        "source_task_id",
        "previous_status",
        "target_status",
        "reason_code",
        "dependency_statuses",
        "failed_dependency_ids",
        "reconciled_at",
    }
)
_FAILURE_STATUSES = frozenset(
    {
        "missing",
        "failed",
        "verification_failed",
        "cancelled",
        "aborted",
        "timeout",
        "skipped",
        "archived",
    }
)
_MAX_DEPENDENCIES = 1_024


@dataclass(frozen=True)
class RecoveryDependencyReconciliationWriteAuthority:
    task_id: str
    source_task_id: str
    previous_status: str
    target_status: str
    reason_code: str
    dependency_statuses: tuple[tuple[str, str], ...]
    failed_dependency_ids: tuple[str, ...]
    reconciled_at: float


_ACTIVE_AUTHORITY: ContextVar[
    RecoveryDependencyReconciliationWriteAuthority | None
] = ContextVar(
    "ananta_recovery_dependency_reconciliation_write_authority",
    default=None,
)


def _identifier(value: Any) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized.encode("utf-8")) > 256
    ):
        raise ValueError(
            "recovery_dependency_reconciliation_authority_invalid"
        )
    return normalized


def _authority(
    *,
    task_id: str,
    marker: Any,
) -> RecoveryDependencyReconciliationWriteAuthority:
    if not isinstance(marker, Mapping):
        raise ValueError(
            "recovery_dependency_reconciliation_authority_invalid"
        )
    value = dict(marker)
    normalized_task_id = _identifier(task_id)
    raw_statuses = value.get("dependency_statuses")
    raw_failed_ids = value.get("failed_dependency_ids")
    if (
        set(value) != RECOVERY_DEPENDENCY_RECONCILIATION_FIELDS
        or value.get("schema")
        != RECOVERY_DEPENDENCY_RECONCILIATION_SCHEMA
        or not isinstance(raw_statuses, list)
        or not raw_statuses
        or len(raw_statuses) > _MAX_DEPENDENCIES
        or not isinstance(raw_failed_ids, list)
        or not raw_failed_ids
        or len(raw_failed_ids) > _MAX_DEPENDENCIES
    ):
        raise ValueError(
            "recovery_dependency_reconciliation_authority_invalid"
        )
    dependency_statuses: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    for entry in raw_statuses:
        if (
            not isinstance(entry, Mapping)
            or set(entry) != {"task_id", "status"}
        ):
            raise ValueError(
                "recovery_dependency_reconciliation_authority_invalid"
            )
        dependency_id = _identifier(entry.get("task_id"))
        status = str(entry.get("status") or "").strip().lower()
        if (
            dependency_id in seen_ids
            or not status
            or len(status.encode("utf-8")) > 64
        ):
            raise ValueError(
                "recovery_dependency_reconciliation_authority_invalid"
            )
        seen_ids.add(dependency_id)
        dependency_statuses.append((dependency_id, status))
    failed_dependency_ids = tuple(
        _identifier(value) for value in raw_failed_ids
    )
    expected_failed_ids = tuple(
        dependency_id
        for dependency_id, status in dependency_statuses
        if status in _FAILURE_STATUSES
    )
    try:
        reconciled_at = float(value.get("reconciled_at"))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "recovery_dependency_reconciliation_authority_invalid"
        ) from exc
    authority = RecoveryDependencyReconciliationWriteAuthority(
        task_id=normalized_task_id,
        source_task_id=_identifier(
            value.get("source_task_id")
        ),
        previous_status=str(
            value.get("previous_status") or ""
        )
        .strip()
        .lower(),
        target_status=str(value.get("target_status") or "")
        .strip()
        .lower(),
        reason_code=str(value.get("reason_code") or "").strip(),
        dependency_statuses=tuple(dependency_statuses),
        failed_dependency_ids=failed_dependency_ids,
        reconciled_at=reconciled_at,
    )
    if (
        str(value.get("task_id") or "").strip()
        != normalized_task_id
        or authority.previous_status
        not in {"blocked", "blocked_by_dependency"}
        or authority.target_status != "failed"
        or authority.reason_code != "recovery_dependency_terminal"
        or failed_dependency_ids != expected_failed_ids
        or len(set(failed_dependency_ids))
        != len(failed_dependency_ids)
        or not math.isfinite(authority.reconciled_at)
        or authority.reconciled_at <= 0.0
    ):
        raise ValueError(
            "recovery_dependency_reconciliation_authority_invalid"
        )
    return authority


@contextmanager
def authorize_recovery_dependency_reconciliation_write(
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


def recovery_dependency_reconciliation_write_authorized(
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
    "RECOVERY_DEPENDENCY_RECONCILIATION_FIELDS",
    "RECOVERY_DEPENDENCY_RECONCILIATION_SCHEMA",
    "RecoveryDependencyReconciliationWriteAuthority",
    "authorize_recovery_dependency_reconciliation_write",
    "recovery_dependency_reconciliation_write_authorized",
]
