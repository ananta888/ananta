"""Rotatable source-access HMAC keyring composition.

Production reads secrets exclusively from an owner-only managed file.  The
explicit compose-safe fallback derives a domain-separated key from Compose's
existing development secret and is unavailable in every other profile.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
from types import MappingProxyType

from ananta_contracts.file_credentials import (
    FileCredentialConfigurationError,
    read_file_managed_bytes,
)

from agent.services.source_access_manifest_signing import SourceAccessSigningKey


SOURCE_ACCESS_KEYRING_SCHEMA = "ananta.source-access-hmac-keyring.v1"
SOURCE_ACCESS_KEYRING_FILE_ENV = "ANANTA_SOURCE_ACCESS_KEYRING_FILE"
SOURCE_ACCESS_COMPOSE_DERIVATION_ENV = (
    "ANANTA_SOURCE_ACCESS_ALLOW_COMPOSE_SECRET_DERIVATION"
)
KNOWLEDGE_INDEX_WORKER_ID_ENV = "ANANTA_KNOWLEDGE_INDEX_WORKER_ID"
_TRUE = frozenset({"1", "true", "yes", "on"})
_LOCAL_COMPOSE_MODES = frozenset({"role", "agent-only"})


class SourceAccessManifestKeyringError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SourceAccessManifestKeyring:
    active_signing_key: SourceAccessSigningKey
    verification_keys: Mapping[str, bytes] = field(repr=False)
    local_compose_derived: bool = False

    def __post_init__(self) -> None:
        keys = {str(key_id): bytes(secret) for key_id, secret in self.verification_keys.items()}
        if keys.get(self.active_signing_key.key_id) != self.active_signing_key.secret:
            raise SourceAccessManifestKeyringError(
                "source_access_active_signing_key_missing"
            )
        for key_id, secret in keys.items():
            SourceAccessSigningKey(key_id=key_id, secret=secret)
        object.__setattr__(self, "verification_keys", MappingProxyType(keys))


def load_source_access_manifest_keyring(
    environment: Mapping[str, str] | None = None,
) -> SourceAccessManifestKeyring:
    source = os.environ if environment is None else environment
    path = str(source.get(SOURCE_ACCESS_KEYRING_FILE_ENV) or "").strip()
    if path:
        return _load_file_keyring(path)
    if _compose_derivation_allowed(source):
        return _derive_compose_keyring(source)
    raise SourceAccessManifestKeyringError("source_access_keyring_required")


def resolve_knowledge_index_worker_id(
    environment: Mapping[str, str] | None = None,
) -> str:
    source = os.environ if environment is None else environment
    worker_id = str(source.get(KNOWLEDGE_INDEX_WORKER_ID_ENV) or "").strip()
    if not worker_id:
        raise SourceAccessManifestKeyringError(
            "knowledge_index_authenticated_worker_id_required"
        )
    return worker_id


def _load_file_keyring(path: str) -> SourceAccessManifestKeyring:
    try:
        raw = read_file_managed_bytes(
            path,
            description="source-access HMAC keyring",
            max_bytes=65_536,
            require_owner_only=True,
        )
    except FileCredentialConfigurationError as exc:
        raise SourceAccessManifestKeyringError(
            "source_access_keyring_unsafe"
        ) from exc
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SourceAccessManifestKeyringError(
            "source_access_keyring_invalid"
        ) from exc
    if not isinstance(decoded, Mapping) or set(decoded) != {
        "schema",
        "active_key_id",
        "keys",
    }:
        raise SourceAccessManifestKeyringError("source_access_keyring_invalid")
    if decoded.get("schema") != SOURCE_ACCESS_KEYRING_SCHEMA:
        raise SourceAccessManifestKeyringError("source_access_keyring_invalid")
    active_key_id = str(decoded.get("active_key_id") or "").strip()
    raw_keys = decoded.get("keys")
    if not isinstance(raw_keys, Mapping) or not raw_keys:
        raise SourceAccessManifestKeyringError("source_access_keyring_invalid")
    keys: dict[str, bytes] = {}
    try:
        for raw_key_id, encoded_secret in raw_keys.items():
            key_id = str(raw_key_id).strip()
            if not isinstance(encoded_secret, str):
                raise ValueError("key material must be base64 text")
            secret = base64.b64decode(encoded_secret, validate=True)
            key = SourceAccessSigningKey(key_id=key_id, secret=secret)
            keys[key.key_id] = key.secret
        active_key = SourceAccessSigningKey(
            key_id=active_key_id,
            secret=keys[active_key_id],
        )
    except (KeyError, ValueError, binascii.Error) as exc:
        raise SourceAccessManifestKeyringError(
            "source_access_keyring_invalid"
        ) from exc
    return SourceAccessManifestKeyring(
        active_signing_key=active_key,
        verification_keys=keys,
    )


def _compose_derivation_allowed(source: Mapping[str, str]) -> bool:
    enabled = str(source.get(SOURCE_ACCESS_COMPOSE_DERIVATION_ENV) or "").strip().lower()
    return (
        enabled in _TRUE
        and str(source.get("ANANTA_RUNTIME_PROFILE") or "").strip()
        == "compose-safe"
        and str(source.get("ANANTA_QUICKSTART_MODE") or "").strip()
        in _LOCAL_COMPOSE_MODES
    )


def _derive_compose_keyring(source: Mapping[str, str]) -> SourceAccessManifestKeyring:
    seed = str(source.get("SECRET_KEY") or "").encode("utf-8")
    if len(seed) < 32:
        raise SourceAccessManifestKeyringError(
            "source_access_compose_secret_invalid"
        )
    secret = hmac.new(
        seed,
        b"ananta.source-access-manifest.hmac.v1",
        hashlib.sha256,
    ).digest()
    key_id = f"compose-{hashlib.sha256(seed).hexdigest()[:16]}"
    active_key = SourceAccessSigningKey(key_id=key_id, secret=secret)
    return SourceAccessManifestKeyring(
        active_signing_key=active_key,
        verification_keys={key_id: secret},
        local_compose_derived=True,
    )


__all__ = [
    "KNOWLEDGE_INDEX_WORKER_ID_ENV",
    "SOURCE_ACCESS_COMPOSE_DERIVATION_ENV",
    "SOURCE_ACCESS_KEYRING_FILE_ENV",
    "SOURCE_ACCESS_KEYRING_SCHEMA",
    "SourceAccessManifestKeyring",
    "SourceAccessManifestKeyringError",
    "load_source_access_manifest_keyring",
    "resolve_knowledge_index_worker_id",
]
