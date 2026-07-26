"""Task-bound capability for one Hub Recovery result publication."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Mapping


@dataclass(frozen=True)
class RecoveryResultCommitWriteAuthority:
    task_id: str
    phase: str
    lease_revision: int
    token_digest: str
    request_fingerprint: str
    accepted_result_status: str
    accepted_result_digest: str


_ACTIVE_AUTHORITY: ContextVar[
    RecoveryResultCommitWriteAuthority | None
] = ContextVar(
    "ananta_recovery_result_commit_write_authority",
    default=None,
)


def _lease(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sha256_hex(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return bool(
        len(normalized) == 64
        and all(character in "0123456789abcdef" for character in normalized)
    )


def _authority(
    *,
    task_id: str,
    lease: Any,
) -> RecoveryResultCommitWriteAuthority:
    value = _lease(lease)
    normalized_task_id = str(task_id or "").strip()
    try:
        revision = int(value.get("revision") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "recovery_result_commit_authority_invalid"
        ) from exc
    authority = RecoveryResultCommitWriteAuthority(
        task_id=normalized_task_id,
        phase=str(
            value.get("accepted_result_phase") or ""
        ).strip(),
        lease_revision=revision,
        token_digest=str(
            value.get("token_digest") or ""
        ).strip(),
        request_fingerprint=str(
            value.get("request_fingerprint") or ""
        ).strip(),
        accepted_result_status=str(
            value.get("accepted_result_status") or ""
        ).strip(),
        accepted_result_digest=str(
            value.get("accepted_result_digest") or ""
        ).strip(),
    )
    if (
        not authority.task_id
        or authority.phase != "execute"
        or authority.lease_revision < 1
        or not _sha256_hex(authority.token_digest)
        or not _sha256_hex(authority.request_fingerprint)
        or authority.accepted_result_status
        not in {"completed", "verification_failed"}
        or not _sha256_hex(authority.accepted_result_digest)
    ):
        raise ValueError(
            "recovery_result_commit_authority_invalid"
        )
    return authority


@contextmanager
def authorize_recovery_result_commit_write(
    *,
    task_id: str,
    lease: Any,
) -> Iterator[None]:
    """Authorize the exact execute acceptance built by ``result_guard``."""

    authority = _authority(task_id=task_id, lease=lease)
    token = _ACTIVE_AUTHORITY.set(authority)
    try:
        yield
    finally:
        _ACTIVE_AUTHORITY.reset(token)


def recovery_result_commit_write_authorized(
    *,
    task_id: str,
    lease: Any,
) -> bool:
    try:
        expected = _authority(task_id=task_id, lease=lease)
    except ValueError:
        return False
    return _ACTIVE_AUTHORITY.get() == expected


__all__ = [
    "RecoveryResultCommitWriteAuthority",
    "authorize_recovery_result_commit_write",
    "recovery_result_commit_write_authorized",
]
