"""Hub-only signing and verification for semantic task leases."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any, Callable, Mapping, Protocol

from ananta_contracts.semantic_compute import (
    LEASE_SCHEMA,
    SemanticComputeContractError,
    canonical_json,
    validate_task_lease,
)


class SemanticTaskLeaseAuthorityError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class SemanticTaskLeaseAuthorityPort(Protocol):
    def issue(self, lease: object, *, room_id: str | None = None) -> dict[str, Any]: ...

    def verify(
        self,
        raw: object,
        *,
        lease: object,
        expected_executor_id: str,
        expected_audience: str,
    ) -> dict[str, Any]: ...


class HubSemanticTaskLeaseAuthority:
    """HMAC authority whose secret remains exclusively inside the Hub."""

    def __init__(
        self,
        secret: bytes | None,
        *,
        key_id: str = "semantic-task-lease-v1",
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._secret = bytes(secret) if secret is not None else None
        self._key_id = str(key_id)
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        clock_ms: Callable[[], int] | None = None,
    ) -> "HubSemanticTaskLeaseAuthority":
        source = os.environ if env is None else env
        secret = str(source.get("ANANTA_SEMANTIC_COMPUTE_SIGNING_KEY") or "").encode()
        return cls(secret or None, clock_ms=clock_ms)

    def issue(self, lease: object, *, room_id: str | None = None) -> dict[str, Any]:
        self._require_secret()
        issued_at_ms = int(float(getattr(lease, "issued_at")) * 1_000)
        expires_at_ms = int(float(getattr(lease, "expires_at")) * 1_000)
        deadline_at_ms = int(float(getattr(lease, "deadline_at")) * 1_000)
        unsigned: dict[str, Any] = {
            "schema": LEASE_SCHEMA,
            "lease_id": str(getattr(lease, "id")),
            "contract_id": str(getattr(lease, "contract_id")),
            "contract_digest": str(getattr(lease, "contract_digest")),
            "session_id": str(getattr(lease, "session_id")),
            **({"room_id": room_id} if room_id is not None else {}),
            "epoch": int(getattr(lease, "epoch")),
            "task_type": str(getattr(lease, "task_type")),
            "role": str(getattr(lease, "role")),
            "executor_id": str(getattr(lease, "executor_id")),
            "audience": str(getattr(lease, "audience")),
            "sequence_start": int(getattr(lease, "sequence_start")),
            "sequence_end": int(getattr(lease, "sequence_end")),
            "fencing_token": int(getattr(lease, "fencing_token")),
            "resource_budget": dict(getattr(lease, "resource_budget") or {}),
            "issued_at_ms": issued_at_ms,
            "expires_at_ms": expires_at_ms,
            "deadline_ms": max(1, min(20_000, deadline_at_ms - issued_at_ms)),
            "issuer": "hub",
        }
        signature = hmac.new(self._secret, canonical_json(unsigned), hashlib.sha256).hexdigest()  # type: ignore[arg-type]
        signed = {
            **unsigned,
            "signature": {
                "algorithm": "hmac-sha256",
                "key_id": self._key_id,
                "value": signature,
            },
        }
        try:
            return validate_task_lease(signed)
        except SemanticComputeContractError as exc:
            raise SemanticTaskLeaseAuthorityError(exc.reason_code) from exc

    def verify(
        self,
        raw: object,
        *,
        lease: object,
        expected_executor_id: str,
        expected_audience: str,
    ) -> dict[str, Any]:
        self._require_secret()
        try:
            normalized = validate_task_lease(
                raw,
                now_ms=self._clock_ms(),
                expected_audience=expected_audience,
            )
        except SemanticComputeContractError as exc:
            raise SemanticTaskLeaseAuthorityError(exc.reason_code) from exc
        signature = dict(normalized["signature"])
        if signature.get("algorithm") != "hmac-sha256" or signature.get("key_id") != self._key_id:
            raise SemanticTaskLeaseAuthorityError("task_lease_signing_key_unknown")
        unsigned = {key: value for key, value in normalized.items() if key != "signature"}
        expected = hmac.new(self._secret, canonical_json(unsigned), hashlib.sha256).hexdigest()  # type: ignore[arg-type]
        if not hmac.compare_digest(expected, str(signature.get("value") or "")):
            raise SemanticTaskLeaseAuthorityError("task_lease_signature_invalid")
        bindings = {
            "lease_id": str(getattr(lease, "id")),
            "contract_id": str(getattr(lease, "contract_id")),
            "contract_digest": str(getattr(lease, "contract_digest")),
            "session_id": str(getattr(lease, "session_id")),
            "epoch": int(getattr(lease, "epoch")),
            "task_type": str(getattr(lease, "task_type")),
            "role": str(getattr(lease, "role")),
            "executor_id": expected_executor_id,
            "audience": expected_audience,
            "sequence_start": int(getattr(lease, "sequence_start")),
            "sequence_end": int(getattr(lease, "sequence_end")),
            "fencing_token": int(getattr(lease, "fencing_token")),
            "resource_budget": dict(getattr(lease, "resource_budget") or {}),
            "issued_at_ms": int(float(getattr(lease, "issued_at")) * 1_000),
            "expires_at_ms": int(float(getattr(lease, "expires_at")) * 1_000),
        }
        if any(normalized.get(field) != value for field, value in bindings.items()):
            raise SemanticTaskLeaseAuthorityError("task_lease_binding_mismatch")
        return normalized

    def _require_secret(self) -> None:
        if self._secret is None or len(self._secret) < 32:
            raise SemanticTaskLeaseAuthorityError("task_lease_signer_unavailable")


__all__ = [
    "HubSemanticTaskLeaseAuthority",
    "SemanticTaskLeaseAuthorityError",
    "SemanticTaskLeaseAuthorityPort",
]
