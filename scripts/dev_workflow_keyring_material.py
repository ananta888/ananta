"""Cryptographic material generation for development workflow keyrings."""

from __future__ import annotations

import base64
import secrets
from typing import Any

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent.services.source_access_manifest_keyring import SOURCE_ACCESS_KEYRING_SCHEMA
from ananta_contracts.runtime_authorization_crypto import (
    ED25519_ALGORITHM,
    ED25519_SIGNING_KEYRING_SCHEMA,
    Ed25519SigningKeyRing,
)
from scripts.dev_workflow_keyring_contract import (
    _AUTH_KEY_ID,
    _DISPATCH_KEY_ID,
    _SOURCE_ACCESS_KEY_ID,
    DevWorkflowKeyringBootstrapError,
    WorkerRegistrationSpec,
    _registration_document,
)


def _generate_documents(
    *,
    worker_specs: tuple[WorkerRegistrationSpec, ...],
) -> dict[str, Any]:
    private = Ed25519PrivateKey.generate()
    private_seed = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    encoded_private_seed = _encode_base64(private_seed)
    signer = Ed25519SigningKeyRing(
        {_AUTH_KEY_ID: encoded_private_seed},
        active_key_id=_AUTH_KEY_ID,
    )
    return {
        "signing": {
            "schema": ED25519_SIGNING_KEYRING_SCHEMA,
            "algorithm": ED25519_ALGORITHM,
            "active_key_id": _AUTH_KEY_ID,
            "private_keys": {
                _AUTH_KEY_ID: encoded_private_seed,
            },
        },
        "verification": signer.verification_mapping(),
        "dispatch": {
            "active_key_id": _DISPATCH_KEY_ID,
            "keys": {
                _DISPATCH_KEY_ID: Fernet.generate_key().decode("ascii"),
            },
        },
        **_generate_worker_identity_documents(worker_specs=worker_specs),
        **_generate_source_access_documents(),
    }


def _generate_worker_identity_documents(
    *,
    worker_specs: tuple[WorkerRegistrationSpec, ...],
) -> dict[str, Any]:
    tokens = {
        "hub_service_token": secrets.token_urlsafe(48),
        "hub_session_key": secrets.token_urlsafe(48),
        "alpha_service_token": secrets.token_urlsafe(48),
        "alpha_registration_token": secrets.token_urlsafe(48),
        "alpha_session_key": secrets.token_urlsafe(48),
        "beta_service_token": secrets.token_urlsafe(48),
        "beta_registration_token": secrets.token_urlsafe(48),
        "beta_session_key": secrets.token_urlsafe(48),
    }
    tokens["registration_keyring"] = _registration_document(
        tokens,
        worker_specs=worker_specs,
    )
    return tokens


def _generate_source_access_documents() -> dict[str, Any]:
    keyring = {
        "schema": SOURCE_ACCESS_KEYRING_SCHEMA,
        "active_key_id": _SOURCE_ACCESS_KEY_ID,
        "keys": {_SOURCE_ACCESS_KEY_ID: _encode_base64(secrets.token_bytes(32))},
    }
    return {
        "source_access_keyring": keyring,
    }


def _encode_base64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _worker_specs(
    *,
    alpha_worker_id: str,
    beta_worker_id: str,
) -> tuple[WorkerRegistrationSpec, ...]:
    normalized_ids = tuple(str(value or "").strip() for value in (alpha_worker_id, beta_worker_id))
    if len(set(normalized_ids)) != 2 or any(
        not value
        or len(value.encode("utf-8")) > 256
        or "\x00" in value
        or any(character in value for character in "\r\n")
        for value in normalized_ids
    ):
        raise DevWorkflowKeyringBootstrapError("development Worker identifiers are invalid")
    return (
        WorkerRegistrationSpec(
            logical_name="alpha",
            worker_id=normalized_ids[0],
            worker_url="http://ai-agent-alpha:5000",
        ),
        WorkerRegistrationSpec(
            logical_name="beta",
            worker_id=normalized_ids[1],
            worker_url="http://ai-agent-beta:5000",
        ),
    )
