"""Exact Hub capability for one Recovery dispatch abort commit."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Mapping


@dataclass(frozen=True)
class RecoveryDispatchAbortWriteAuthority:
    task_id: str
    current_revision: int
    proposed_revision: int
    phase: str
    token_digest: str
    request_fingerprint: str
    reason_code: str
    target_status: str


_ACTIVE_AUTHORITY: ContextVar[
    RecoveryDispatchAbortWriteAuthority | None
] = ContextVar(
    "ananta_recovery_dispatch_abort_write_authority",
    default=None,
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _revision(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("recovery_dispatch_abort_authority_invalid")
    try:
        revision = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "recovery_dispatch_abort_authority_invalid"
        ) from exc
    if revision < 0:
        raise ValueError(
            "recovery_dispatch_abort_authority_invalid"
        )
    return revision


def _authority(
    *,
    task_id: str,
    current_lease: Any,
    proposed_lease: Any,
    target_status: str,
) -> RecoveryDispatchAbortWriteAuthority:
    current = _mapping(current_lease)
    proposed = _mapping(proposed_lease)
    normalized_task_id = str(task_id or "").strip()
    current_revision = _revision(current.get("revision"))
    proposed_revision = _revision(proposed.get("revision"))
    authority = RecoveryDispatchAbortWriteAuthority(
        task_id=normalized_task_id,
        current_revision=current_revision,
        proposed_revision=proposed_revision,
        phase=str(
            current.get("phase")
            or current.get("accepted_result_phase")
            or ""
        ).strip(),
        token_digest=str(
            current.get("token_digest") or ""
        ).strip(),
        request_fingerprint=str(
            current.get("request_fingerprint") or ""
        ).strip(),
        reason_code=str(
            proposed.get("revocation_reason") or ""
        ).strip(),
        target_status=str(target_status or "").strip().lower(),
    )
    if (
        not authority.task_id
        or authority.phase not in {"propose", "execute"}
        or proposed.get("state") != "revoked"
        or authority.proposed_revision
        != authority.current_revision + 1
        or not authority.reason_code
        or len(authority.reason_code.encode("utf-8")) > 160
        or authority.target_status
        not in {
            "failed",
            "cancelled",
            "verification_failed",
            "aborted",
            "timeout",
        }
    ):
        raise ValueError(
            "recovery_dispatch_abort_authority_invalid"
        )
    return authority


@contextmanager
def authorize_recovery_dispatch_abort_write(
    *,
    task_id: str,
    current_lease: Any,
    proposed_lease: Any,
    target_status: str,
) -> Iterator[None]:
    authority = _authority(
        task_id=task_id,
        current_lease=current_lease,
        proposed_lease=proposed_lease,
        target_status=target_status,
    )
    token = _ACTIVE_AUTHORITY.set(authority)
    try:
        yield
    finally:
        _ACTIVE_AUTHORITY.reset(token)


def recovery_dispatch_abort_write_authorized(
    *,
    task_id: str,
    current_lease: Any,
    proposed_lease: Any,
    target_status: str,
) -> bool:
    try:
        expected = _authority(
            task_id=task_id,
            current_lease=current_lease,
            proposed_lease=proposed_lease,
            target_status=target_status,
        )
    except ValueError:
        return False
    return _ACTIVE_AUTHORITY.get() == expected


__all__ = [
    "RecoveryDispatchAbortWriteAuthority",
    "authorize_recovery_dispatch_abort_write",
    "recovery_dispatch_abort_write_authorized",
]
