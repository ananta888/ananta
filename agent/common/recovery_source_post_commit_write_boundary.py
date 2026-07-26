"""Task-bound capability for Recovery source post-commit marker writes."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Mapping


@dataclass(frozen=True)
class RecoverySourcePostCommitWriteAuthority:
    task_id: str
    current_digest: str
    proposed_digest: str


_ACTIVE_AUTHORITY: ContextVar[
    RecoverySourcePostCommitWriteAuthority | None
] = ContextVar(
    "ananta_recovery_source_post_commit_write_authority",
    default=None,
)


def _marker_digest(value: Any) -> str:
    marker = dict(value) if isinstance(value, Mapping) else {}
    encoded = json.dumps(
        marker,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _authority(
    *,
    task_id: str,
    current: Any,
    proposed: Any,
) -> RecoverySourcePostCommitWriteAuthority:
    normalized_task_id = str(task_id or "").strip()
    if (
        not normalized_task_id
        or not isinstance(current, Mapping)
        or not isinstance(proposed, Mapping)
    ):
        raise ValueError(
            "recovery_source_post_commit_authority_invalid"
        )
    return RecoverySourcePostCommitWriteAuthority(
        task_id=normalized_task_id,
        current_digest=_marker_digest(current),
        proposed_digest=_marker_digest(proposed),
    )


@contextmanager
def authorize_recovery_source_post_commit_write(
    *,
    task_id: str,
    current: Any,
    proposed: Any,
) -> Iterator[None]:
    authority = _authority(
        task_id=task_id,
        current=current,
        proposed=proposed,
    )
    token = _ACTIVE_AUTHORITY.set(authority)
    try:
        yield
    finally:
        _ACTIVE_AUTHORITY.reset(token)


def recovery_source_post_commit_write_authorized(
    *,
    task_id: str,
    current: Any,
    proposed: Any,
) -> bool:
    try:
        expected = _authority(
            task_id=task_id,
            current=current,
            proposed=proposed,
        )
    except ValueError:
        return False
    return _ACTIVE_AUTHORITY.get() == expected


__all__ = [
    "RecoverySourcePostCommitWriteAuthority",
    "authorize_recovery_source_post_commit_write",
    "recovery_source_post_commit_write_authorized",
]
