"""Worker-only composition for vector-index task verification."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from ananta_contracts.file_credentials import (
    FileCredentialConfigurationError,
    read_file_managed_bytes,
)
from ananta_contracts.runtime_authorization_crypto import (
    Ed25519VerificationKeyRing,
    RuntimeAuthorizationCryptoError,
)
from ananta_contracts.vector_index_task_attestation import (
    VectorIndexTaskVerifier,
)

VECTOR_INDEX_TASK_VERIFICATION_KEYRING_ENV = "ANANTA_VECTOR_INDEX_TASK_VERIFICATION_KEYRING_FILE"


class UnavailableVectorIndexTaskVerifier:
    """Fail every vector-index task when no public keyring is configured."""

    def verify(self, envelope: Mapping[str, Any]) -> None:
        del envelope
        raise ValueError("vector_index_task_verification_keyring_required")


def load_vector_index_task_verifier(
    environment: Mapping[str, str] | None = None,
) -> VectorIndexTaskVerifier | UnavailableVectorIndexTaskVerifier:
    """Load a public-only Ed25519 keyring; private fields are rejected."""

    source = os.environ if environment is None else environment
    path = str(source.get(VECTOR_INDEX_TASK_VERIFICATION_KEYRING_ENV) or "").strip()
    if not path:
        return UnavailableVectorIndexTaskVerifier()
    try:
        raw = read_file_managed_bytes(
            path,
            description="vector-index Worker verification keyring",
            max_bytes=65_536,
        )
    except FileCredentialConfigurationError as exc:
        raise ValueError("vector_index_task_verification_keyring_unsafe") from exc
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("vector_index_task_verification_keyring_invalid") from exc
    if not isinstance(decoded, Mapping):
        raise ValueError("vector_index_task_verification_keyring_invalid")
    try:
        key_ring = Ed25519VerificationKeyRing.from_mapping(decoded)
    except RuntimeAuthorizationCryptoError as exc:
        raise ValueError("vector_index_task_verification_keyring_invalid") from exc
    return VectorIndexTaskVerifier(key_ring)


__all__ = [
    "VECTOR_INDEX_TASK_VERIFICATION_KEYRING_ENV",
    "UnavailableVectorIndexTaskVerifier",
    "load_vector_index_task_verifier",
]
