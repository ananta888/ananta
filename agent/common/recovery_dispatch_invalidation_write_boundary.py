"""Exact Hub capability for one Recovery lease invalidation."""

from __future__ import annotations

import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Mapping


@dataclass(frozen=True)
class RecoveryDispatchInvalidationWriteAuthority:
    task_id: str
    current_revision: int
    proposed_revision: int
    current_state: str
    proposed_state: str
    phase: str
    token_digest: str
    request_fingerprint: str
    reason_code: str
    invalidated_at: float


_ACTIVE_AUTHORITY: ContextVar[
    RecoveryDispatchInvalidationWriteAuthority | None
] = ContextVar(
    "ananta_recovery_dispatch_invalidation_write_authority",
    default=None,
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _revision(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError(
            "recovery_dispatch_invalidation_authority_invalid"
        )
    try:
        revision = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "recovery_dispatch_invalidation_authority_invalid"
        ) from exc
    if revision < 1:
        raise ValueError(
            "recovery_dispatch_invalidation_authority_invalid"
        )
    return revision


def _sha256_hex(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if (
        len(normalized) != 64
        or any(
            character not in "0123456789abcdef"
            for character in normalized
        )
    ):
        raise ValueError(
            "recovery_dispatch_invalidation_authority_invalid"
        )
    return normalized


def _authority(
    *,
    task_id: str,
    current_lease: Any,
    proposed_lease: Any,
) -> RecoveryDispatchInvalidationWriteAuthority:
    current = _mapping(current_lease)
    proposed = _mapping(proposed_lease)
    normalized_task_id = str(task_id or "").strip()
    current_revision = _revision(current.get("revision"))
    proposed_revision = _revision(proposed.get("revision"))
    current_state = str(current.get("state") or "").strip()
    proposed_state = str(proposed.get("state") or "").strip()
    reason_field = (
        "revocation_reason"
        if proposed_state == "revoked"
        else "cancellation_reason"
    )
    timestamp_field = (
        "revoked_at"
        if proposed_state == "revoked"
        else "cancelled_at"
    )
    reason_code = str(
        proposed.get(reason_field) or ""
    ).strip()
    try:
        invalidated_at = float(proposed.get(timestamp_field))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "recovery_dispatch_invalidation_authority_invalid"
        ) from exc
    authority = RecoveryDispatchInvalidationWriteAuthority(
        task_id=normalized_task_id,
        current_revision=current_revision,
        proposed_revision=proposed_revision,
        current_state=current_state,
        proposed_state=proposed_state,
        phase=str(current.get("phase") or "").strip(),
        token_digest=_sha256_hex(current.get("token_digest")),
        request_fingerprint=_sha256_hex(
            current.get("request_fingerprint")
        ),
        reason_code=reason_code,
        invalidated_at=invalidated_at,
    )
    if (
        not authority.task_id
        or authority.current_state
        not in {"active", "worker_admitted"}
        or authority.proposed_state
        not in {"revoked", "cancelled"}
        or authority.proposed_revision
        != authority.current_revision + 1
        or authority.phase not in {"propose", "execute"}
        or not authority.reason_code
        or len(authority.reason_code.encode("utf-8")) > 160
        or not math.isfinite(authority.invalidated_at)
        or authority.invalidated_at <= 0.0
    ):
        raise ValueError(
            "recovery_dispatch_invalidation_authority_invalid"
        )
    return authority


@contextmanager
def authorize_recovery_dispatch_invalidation_write(
    *,
    task_id: str,
    current_lease: Any,
    proposed_lease: Any,
) -> Iterator[None]:
    authority = _authority(
        task_id=task_id,
        current_lease=current_lease,
        proposed_lease=proposed_lease,
    )
    token = _ACTIVE_AUTHORITY.set(authority)
    try:
        yield
    finally:
        _ACTIVE_AUTHORITY.reset(token)


def recovery_dispatch_invalidation_write_authorized(
    *,
    task_id: str,
    current_lease: Any,
    proposed_lease: Any,
) -> bool:
    try:
        expected = _authority(
            task_id=task_id,
            current_lease=current_lease,
            proposed_lease=proposed_lease,
        )
    except (TypeError, ValueError):
        return False
    return _ACTIVE_AUTHORITY.get() == expected


__all__ = [
    "RecoveryDispatchInvalidationWriteAuthority",
    "authorize_recovery_dispatch_invalidation_write",
    "recovery_dispatch_invalidation_write_authorized",
]
