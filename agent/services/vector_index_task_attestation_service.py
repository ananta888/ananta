"""Hub-only composition for vector-index task signing."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping

from ananta_contracts.file_credentials import (
    FileCredentialConfigurationError,
    read_file_managed_bytes,
)
from ananta_contracts.runtime_authorization_crypto import (
    Ed25519SigningKeyRing,
    RuntimeAuthorizationCryptoError,
)
from ananta_contracts.vector_index_task_attestation import (
    VectorIndexTaskSigner,
)

VECTOR_INDEX_TASK_SIGNING_KEYRING_ENV = "ANANTA_VECTOR_INDEX_TASK_SIGNING_KEYRING_FILE"


class VectorIndexTaskSigningConfigurationError(RuntimeError):
    """Stable fail-closed Hub signing configuration error."""


def load_vector_index_task_signer(
    environment: Mapping[str, str] | None = None,
) -> VectorIndexTaskSigner:
    """Load the private Ed25519 keyring available only to the Hub."""

    source = os.environ if environment is None else environment
    path = str(source.get(VECTOR_INDEX_TASK_SIGNING_KEYRING_ENV) or "").strip()
    if not path:
        raise VectorIndexTaskSigningConfigurationError("vector_index_task_signing_keyring_required")
    try:
        raw = read_file_managed_bytes(
            path,
            description="vector-index Hub signing keyring",
            max_bytes=65_536,
            require_owner_only=True,
        )
    except FileCredentialConfigurationError as exc:
        raise VectorIndexTaskSigningConfigurationError("vector_index_task_signing_keyring_unsafe") from exc
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise VectorIndexTaskSigningConfigurationError("vector_index_task_signing_keyring_invalid") from exc
    if not isinstance(decoded, Mapping):
        raise VectorIndexTaskSigningConfigurationError("vector_index_task_signing_keyring_invalid")
    try:
        key_ring = Ed25519SigningKeyRing.from_mapping(decoded)
    except RuntimeAuthorizationCryptoError as exc:
        raise VectorIndexTaskSigningConfigurationError("vector_index_task_signing_keyring_invalid") from exc
    return VectorIndexTaskSigner(key_ring)


__all__ = [
    "VECTOR_INDEX_TASK_SIGNING_KEYRING_ENV",
    "VectorIndexTaskSigningConfigurationError",
    "load_vector_index_task_signer",
]
