"""Task-bound Hub capability for Goal-owner terminal invalidation."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

RECOVERY_OWNER_TERMINAL_SCHEMA = (
    "ananta.recovery_owner_terminal_invalidation.v1"
)


@dataclass(frozen=True)
class RecoveryOwnerTerminalWriteAuthority:
    task_id: str
    marker_digest: str


_ACTIVE_AUTHORITY: ContextVar[
    RecoveryOwnerTerminalWriteAuthority | None
] = ContextVar(
    "ananta_recovery_owner_terminal_write_authority",
    default=None,
)


def _marker_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _authority(
    *,
    task_id: str,
    marker: Any,
) -> RecoveryOwnerTerminalWriteAuthority:
    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id or not isinstance(marker, Mapping):
        raise ValueError(
            "recovery_owner_terminal_authority_invalid"
        )
    return RecoveryOwnerTerminalWriteAuthority(
        task_id=normalized_task_id,
        marker_digest=_marker_digest(marker),
    )


@contextmanager
def authorize_recovery_owner_terminal_write(
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


def recovery_owner_terminal_write_authorized(
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
    "RECOVERY_OWNER_TERMINAL_SCHEMA",
    "RecoveryOwnerTerminalWriteAuthority",
    "authorize_recovery_owner_terminal_write",
    "recovery_owner_terminal_write_authorized",
]
