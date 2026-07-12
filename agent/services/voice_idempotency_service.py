from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any

from agent.repositories.voice_governance import VoiceIdempotencyRepository
from agent.services.voice_governance_domain import (
    VoiceGovernanceError,
    VoicePrincipal,
    stable_payload_hash,
    validate_text,
    voice_idempotency_storage_key,
)

_CLAIM_LOCKS = tuple(threading.Lock() for _index in range(64))


@dataclass(frozen=True)
class VoiceIdempotencyClaim:
    record_id: str
    replayed: bool
    result_metadata: dict[str, Any]
    lease_token: float | None = None


class VoiceIdempotencyService:
    """Scopes mutation replay protection to tenant, subject and operation."""

    def __init__(
        self,
        repository: VoiceIdempotencyRepository | None = None,
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        self._repository = repository or VoiceIdempotencyRepository()
        configured_ttl = int(
            ttl_seconds
            if ttl_seconds is not None
            else os.getenv("VOICE_IDEMPOTENCY_TTL_SECONDS", "86400")
        )
        self._ttl_seconds = max(60, min(configured_ttl, 30 * 24 * 60 * 60))

    def begin(
        self,
        principal: VoicePrincipal,
        *,
        operation: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> VoiceIdempotencyClaim:
        raw_key = str(idempotency_key or "").strip()
        if not raw_key:
            raise VoiceGovernanceError(
                code="voice_governance.idempotency_key_required",
                message="Idempotency-Key header is required",
                status_code=400,
            )
        normalized_key = validate_text(
            raw_key,
            field="idempotency_key",
            max_length=160,
            required=True,
        )
        normalized_key = normalized_key or raw_key
        request_hash = stable_payload_hash(payload)
        lock_scope = f"{principal.tenant_id}\0{principal.subject}\0{operation}\0{normalized_key}"
        claim_lock = _CLAIM_LOCKS[int(stable_payload_hash({"scope": lock_scope})[:8], 16) % len(_CLAIM_LOCKS)]
        # The database uniqueness constraint remains authoritative across Hub
        # processes.  This striped lock also provides deterministic singleflight
        # behavior for concurrent requests handled by one Hub process (and for
        # SQLite-backed deployments that serialize through one connection).
        with claim_lock:
            storage_key = voice_idempotency_storage_key(
                principal,
                operation=operation,
                idempotency_key=normalized_key,
            )
            record, created = self._repository.claim(
                principal,
                operation=operation,
                idempotency_key=storage_key,
                legacy_idempotency_key=normalized_key,
                request_hash=request_hash,
                expires_at=time.time() + self._ttl_seconds,
            )
        return VoiceIdempotencyClaim(
            record_id=record.id,
            replayed=not created,
            result_metadata=dict(record.result_metadata or {}),
            lease_token=float(record.lease_expires_at) if created else None,
        )

    def complete(self, claim: VoiceIdempotencyClaim, result_metadata: dict[str, Any]) -> None:
        if not claim.replayed:
            if claim.lease_token is None:
                raise RuntimeError("active voice idempotency claim has no lease token")
            self._repository.complete(
                claim.record_id,
                lease_token=claim.lease_token,
                result_metadata=result_metadata,
            )

    def abandon(self, claim: VoiceIdempotencyClaim) -> None:
        if not claim.replayed:
            if claim.lease_token is None:
                raise RuntimeError("active voice idempotency claim has no lease token")
            self._repository.release(claim.record_id, lease_token=claim.lease_token)

    def purge_expired(self, *, now: float | None = None) -> int:
        return self._repository.purge_expired(now=now)

    def invalidate_completed_operation(self, operation: str) -> tuple[dict[str, Any], ...]:
        return self._repository.invalidate_completed_operation(operation)
