"""Asymmetric origin binding for Hub-delegated vector-index tasks."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ananta_contracts.runtime_authorization_crypto import (
    ED25519_ALGORITHM,
    Ed25519SigningKeyRing,
    Ed25519VerificationKeyRing,
    RuntimeAuthorizationCryptoError,
)

VECTOR_INDEX_TASK_ATTESTATION_SCHEMA = "ananta.vector_index_task_attestation.v2"
VECTOR_INDEX_TASK_ATTESTATION_FIELD = "hub_attestation"
VECTOR_INDEX_TASK_ATTESTATION_NAMESPACE = "ananta.vector_index_task_attestation.v2"
_ATTESTATION_FIELDS = frozenset({"schema", "algorithm", "key_id", "signature"})


class VectorIndexTaskAttestationError(ValueError):
    """Stable fail-closed error for missing or invalid Hub provenance."""

    def __init__(self) -> None:
        super().__init__("vector_index_task_attestation_invalid")


def _unsigned(envelope: Mapping[str, Any]) -> dict[str, Any]:
    try:
        cloned = json.loads(
            json.dumps(
                dict(envelope),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
        )
    except (TypeError, ValueError) as exc:
        raise VectorIndexTaskAttestationError() from exc
    cloned.pop(VECTOR_INDEX_TASK_ATTESTATION_FIELD, None)
    return cloned


class VectorIndexTaskSigner:
    """Hub-only signer. The public interface intentionally cannot verify."""

    def __init__(self, key_ring: Ed25519SigningKeyRing) -> None:
        self._key_ring = key_ring

    def attest(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        """Return a detached-copy envelope carrying a Hub-origin signature."""

        unsigned = _unsigned(envelope)
        selected_key_id = self._key_ring.active_key_id
        protected_header = {
            "schema": VECTOR_INDEX_TASK_ATTESTATION_SCHEMA,
            "algorithm": ED25519_ALGORITHM,
            "key_id": selected_key_id,
        }
        key_id, signature = self._key_ring.sign(
            namespace=VECTOR_INDEX_TASK_ATTESTATION_NAMESPACE,
            payload=unsigned,
            key_id=selected_key_id,
            protected_header=protected_header,
        )
        if key_id != selected_key_id:
            raise VectorIndexTaskAttestationError()
        return {
            **unsigned,
            VECTOR_INDEX_TASK_ATTESTATION_FIELD: {
                **protected_header,
                "signature": signature,
            },
        }


class VectorIndexTaskVerifier:
    """Worker-only verifier. No signing capability crosses the boundary."""

    def __init__(self, key_ring: Ed25519VerificationKeyRing) -> None:
        self._key_ring = key_ring

    def verify(self, envelope: Mapping[str, Any]) -> None:
        """Reject any missing, malformed or modified task envelope."""

        if not isinstance(envelope, Mapping):
            raise VectorIndexTaskAttestationError()
        attestation = envelope.get(VECTOR_INDEX_TASK_ATTESTATION_FIELD)
        if not isinstance(attestation, Mapping):
            raise VectorIndexTaskAttestationError()
        payload = dict(attestation)
        if (
            set(payload) != _ATTESTATION_FIELDS
            or payload.get("schema") != VECTOR_INDEX_TASK_ATTESTATION_SCHEMA
            or payload.get("algorithm") != ED25519_ALGORITHM
        ):
            raise VectorIndexTaskAttestationError()
        job_id = str(envelope.get("job_id") or "").strip()
        if not job_id:
            raise VectorIndexTaskAttestationError()
        try:
            protected_header = {
                field: payload[field]
                for field in ("schema", "algorithm", "key_id")
            }
            self._key_ring.verify(
                namespace=VECTOR_INDEX_TASK_ATTESTATION_NAMESPACE,
                payload=_unsigned(envelope),
                key_id=str(payload.get("key_id") or ""),
                signature=str(payload.get("signature") or ""),
                contract_id=job_id,
                protected_header=protected_header,
            )
        except RuntimeAuthorizationCryptoError as exc:
            raise VectorIndexTaskAttestationError() from exc


__all__ = [
    "VECTOR_INDEX_TASK_ATTESTATION_FIELD",
    "VECTOR_INDEX_TASK_ATTESTATION_NAMESPACE",
    "VECTOR_INDEX_TASK_ATTESTATION_SCHEMA",
    "VectorIndexTaskAttestationError",
    "VectorIndexTaskSigner",
    "VectorIndexTaskVerifier",
]
