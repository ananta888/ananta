"""Deterministic asymmetric key material for vector-index contract tests."""

from __future__ import annotations

import base64

from ananta_contracts.runtime_authorization_crypto import (
    ED25519_ALGORITHM,
    ED25519_SIGNING_KEYRING_SCHEMA,
    Ed25519SigningKeyRing,
)
from ananta_contracts.vector_index_task_attestation import (
    VectorIndexTaskSigner,
    VectorIndexTaskVerifier,
)

KEY_ID = "vector-index-test-key"
_PRIVATE_SEED = bytes(range(32))
SIGNING_KEYRING_MAPPING = {
    "schema": ED25519_SIGNING_KEYRING_SCHEMA,
    "algorithm": ED25519_ALGORITHM,
    "active_key_id": KEY_ID,
    "private_keys": {
        KEY_ID: base64.b64encode(_PRIVATE_SEED).decode("ascii"),
    },
    "revoked_key_ids": [],
    "revoked_envelope_ids": [],
}
SIGNING_KEY_RING = Ed25519SigningKeyRing.from_mapping(SIGNING_KEYRING_MAPPING)
VERIFICATION_KEYRING_MAPPING = SIGNING_KEY_RING.verification_mapping()
TASK_SIGNER = VectorIndexTaskSigner(SIGNING_KEY_RING)
TASK_VERIFIER = VectorIndexTaskVerifier(SIGNING_KEY_RING.verification_key_ring())

__all__ = [
    "SIGNING_KEYRING_MAPPING",
    "TASK_SIGNER",
    "TASK_VERIFIER",
    "VERIFICATION_KEYRING_MAPPING",
]
