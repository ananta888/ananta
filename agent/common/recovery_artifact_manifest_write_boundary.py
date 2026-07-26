"""Task-bound capability for one Recovery artifact-manifest binding."""

from __future__ import annotations

import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

from ananta_contracts.recovery_artifact_ingress import (
    MAX_RECOVERY_ARTIFACT_COUNT,
    MAX_RECOVERY_ARTIFACT_TOTAL_BYTES,
)

RECOVERY_ARTIFACT_MANIFEST_BINDING_SCHEMA = (
    "ananta.recovery_artifact_manifest_binding.v1"
)
RECOVERY_ARTIFACT_MANIFEST_BINDING_FIELDS = frozenset(
    {
        "schema",
        "task_id",
        "lease_revision",
        "token_digest",
        "request_fingerprint",
        "manifest_digest",
        "artifact_count",
        "total_bytes",
        "bound_at",
    }
)


@dataclass(frozen=True)
class RecoveryArtifactManifestWriteAuthority:
    task_id: str
    lease_revision: int
    token_digest: str
    request_fingerprint: str
    manifest_digest: str
    artifact_count: int
    total_bytes: int
    bound_at: float


_ACTIVE_AUTHORITY: ContextVar[
    RecoveryArtifactManifestWriteAuthority | None
] = ContextVar(
    "ananta_recovery_artifact_manifest_write_authority",
    default=None,
)


def _sha256_hex(value: object) -> str:
    normalized = (
        value.strip().lower()
        if isinstance(value, str)
        else ""
    )
    if (
        len(normalized) != 64
        or any(
            character not in "0123456789abcdef"
            for character in normalized
        )
    ):
        raise ValueError(
            "recovery_artifact_manifest_authority_invalid"
        )
    return normalized


def _bounded_integer(
    value: object,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise ValueError(
            "recovery_artifact_manifest_authority_invalid"
        )
    return value


def _authority(
    *,
    task_id: str,
    lease: Any,
    binding: Any,
) -> RecoveryArtifactManifestWriteAuthority:
    normalized_task_id = str(task_id or "").strip()
    current_lease = (
        dict(lease) if isinstance(lease, Mapping) else {}
    )
    proposed_binding = (
        dict(binding) if isinstance(binding, Mapping) else {}
    )
    if (
        not normalized_task_id
        or set(proposed_binding)
        != RECOVERY_ARTIFACT_MANIFEST_BINDING_FIELDS
        or proposed_binding.get("schema")
        != RECOVERY_ARTIFACT_MANIFEST_BINDING_SCHEMA
        or str(current_lease.get("state") or "").strip()
        != "worker_admitted"
        or str(current_lease.get("phase") or "").strip()
        != "execute"
        or str(current_lease.get("task_id") or "").strip()
        not in {"", normalized_task_id}
        or str(proposed_binding.get("task_id") or "").strip()
        != normalized_task_id
    ):
        raise ValueError(
            "recovery_artifact_manifest_authority_invalid"
        )
    lease_revision = _bounded_integer(
        current_lease.get("revision"),
        minimum=1,
        maximum=2_147_483_647,
    )
    binding_revision = _bounded_integer(
        proposed_binding.get("lease_revision"),
        minimum=1,
        maximum=2_147_483_647,
    )
    token_digest = _sha256_hex(
        current_lease.get("token_digest")
    )
    request_fingerprint = _sha256_hex(
        current_lease.get("request_fingerprint")
    )
    if (
        binding_revision != lease_revision
        or _sha256_hex(
            proposed_binding.get("token_digest")
        )
        != token_digest
        or _sha256_hex(
            proposed_binding.get("request_fingerprint")
        )
        != request_fingerprint
    ):
        raise ValueError(
            "recovery_artifact_manifest_authority_invalid"
        )
    artifact_count = _bounded_integer(
        proposed_binding.get("artifact_count"),
        minimum=1,
        maximum=MAX_RECOVERY_ARTIFACT_COUNT,
    )
    total_bytes = _bounded_integer(
        proposed_binding.get("total_bytes"),
        minimum=0,
        maximum=MAX_RECOVERY_ARTIFACT_TOTAL_BYTES,
    )
    try:
        bound_at = float(proposed_binding.get("bound_at"))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "recovery_artifact_manifest_authority_invalid"
        ) from exc
    if not math.isfinite(bound_at) or bound_at <= 0.0:
        raise ValueError(
            "recovery_artifact_manifest_authority_invalid"
        )
    return RecoveryArtifactManifestWriteAuthority(
        task_id=normalized_task_id,
        lease_revision=lease_revision,
        token_digest=token_digest,
        request_fingerprint=request_fingerprint,
        manifest_digest=_sha256_hex(
            proposed_binding.get("manifest_digest")
        ),
        artifact_count=artifact_count,
        total_bytes=total_bytes,
        bound_at=bound_at,
    )


@contextmanager
def authorize_recovery_artifact_manifest_write(
    *,
    task_id: str,
    lease: Any,
    binding: Any,
) -> Iterator[None]:
    """Authorize exactly one initial manifest binding publication."""

    authority = _authority(
        task_id=task_id,
        lease=lease,
        binding=binding,
    )
    token = _ACTIVE_AUTHORITY.set(authority)
    try:
        yield
    finally:
        _ACTIVE_AUTHORITY.reset(token)


def recovery_artifact_manifest_write_authorized(
    *,
    task_id: str,
    lease: Any,
    binding: Any,
) -> bool:
    try:
        expected = _authority(
            task_id=task_id,
            lease=lease,
            binding=binding,
        )
    except (TypeError, ValueError):
        return False
    return _ACTIVE_AUTHORITY.get() == expected


__all__ = [
    "RECOVERY_ARTIFACT_MANIFEST_BINDING_FIELDS",
    "RECOVERY_ARTIFACT_MANIFEST_BINDING_SCHEMA",
    "RecoveryArtifactManifestWriteAuthority",
    "authorize_recovery_artifact_manifest_write",
    "recovery_artifact_manifest_write_authorized",
]
