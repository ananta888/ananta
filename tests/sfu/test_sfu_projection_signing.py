from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import Flask

from agent.bootstrap.sfu_broadcast_services import initialize_sfu_broadcast_hub_composition
from agent.services.sfu_projection_signing import (
    EncodedSecretSfuProjectionPrivateKeySource,
    Ed25519SfuProjectionSigner,
    FileSfuProjectionPrivateKeySource,
    HmacSfuProjectionSigner,
    SfuProjectionSigningConfigurationError,
    SfuProjectionTrustedKeyset,
)


ENV_NAMES = (
    "ANANTA_SFU_PROJECTION_SIGNING_MODE",
    "ANANTA_SFU_PROJECTION_SIGNING_KEY_ID",
    "ANANTA_SFU_PROJECTION_SIGNING_KEY_VERSION",
    "ANANTA_SFU_PROJECTION_ED25519_PRIVATE_KEY_B64URL",
    "ANANTA_SFU_PROJECTION_ED25519_PRIVATE_KEY_FILE",
    "ANANTA_SFU_PROJECTION_TRUSTED_KEYSET_JSON",
    "ANANTA_SFU_PROJECTION_TRUSTED_KEYSET_FILE",
    "ANANTA_SFU_PROJECTION_ALLOW_LEGACY_HMAC",
)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _private_raw(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )


def _public_raw(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _key(key_id: str, version: int, status: str, material: bytes) -> dict[str, object]:
    return {
        "keyId": key_id,
        "algorithm": "Ed25519",
        "algorithmVersion": 1,
        "keyVersion": version,
        "status": status,
        "format": "raw",
        "keyMaterialBase64Url": _b64(material),
    }


def test_ed25519_signer_binds_versioned_metadata_and_public_key() -> None:
    private = Ed25519PrivateKey.generate()
    signer = Ed25519SfuProjectionSigner(
        EncodedSecretSfuProjectionPrivateKeySource(_b64(_private_raw(private))),
        key_id="hub-projection:v3",
        key_version=3,
    )

    signed = signer.sign("a" * 64)

    private.public_key().verify(
        base64.urlsafe_b64decode(signed.value + "=="),
        b"ananta:sfu-projection:v1:" + b"a" * 64,
    )
    assert (signed.algorithm, signed.algorithm_version, signed.key_version) == (
        "Ed25519",
        1,
        3,
    )
    assert signer.public_key_base64url == _b64(_public_raw(private))


def test_raw_private_key_preserves_binary_boundary_whitespace_bytes() -> None:
    raw_private_key = b"\x0b" + (b"\x01" * 30) + b"\x20"
    signer = Ed25519SfuProjectionSigner(
        EncodedSecretSfuProjectionPrivateKeySource(_b64(raw_private_key)),
        key_id="hub-projection:v1",
        key_version=1,
    )

    signed = signer.sign("c" * 64)

    Ed25519PrivateKey.from_private_bytes(raw_private_key).public_key().verify(
        base64.urlsafe_b64decode(signed.value + "=="),
        b"ananta:sfu-projection:v1:" + b"c" * 64,
    )


def test_keyset_supports_overlap_and_revoke_without_private_material() -> None:
    old = Ed25519PrivateKey.generate()
    active = Ed25519PrivateKey.generate()
    document = {
        "schema": "ananta.sfu-projection-trusted-keyset.v1",
        "keysetVersion": 4,
        "keys": [
            _key("hub-projection:v2", 2, "overlap", _public_raw(old)),
            _key("hub-projection:v3", 3, "active", _public_raw(active)),
            _key("hub-projection:v1", 1, "revoked", _public_raw(old)),
        ],
    }
    keyset = SfuProjectionTrustedKeyset.from_document(document)
    signer = Ed25519SfuProjectionSigner(
        EncodedSecretSfuProjectionPrivateKeySource(_b64(_private_raw(active))),
        key_id="hub-projection:v3",
        key_version=3,
    )

    assert keyset.authorizes(signer) is True
    public = json.dumps(keyset.public(), sort_keys=True)
    assert "private" not in public.casefold()
    assert _b64(_private_raw(active)) not in public


def test_private_key_file_rejects_links_and_broad_permissions(tmp_path) -> None:
    private = tmp_path / "projection.key"
    private.write_bytes(_private_raw(Ed25519PrivateKey.generate()))
    private.chmod(0o644)
    with pytest.raises(SfuProjectionSigningConfigurationError):
        FileSfuProjectionPrivateKeySource(private).load_private_key()
    private.chmod(0o600)
    link = tmp_path / "projection-link.key"
    link.symlink_to(private)
    with pytest.raises(SfuProjectionSigningConfigurationError):
        FileSfuProjectionPrivateKeySource(link).load_private_key()


def test_hmac_adapter_requires_explicit_legacy_mode() -> None:
    with pytest.raises(SfuProjectionSigningConfigurationError):
        HmacSfuProjectionSigner(b"x" * 32, key_id="legacy:v1")
    signer = HmacSfuProjectionSigner(
        b"x" * 32,
        key_id="legacy:v1",
        legacy_mode=True,
    )
    assert signer.sign("b" * 64).algorithm == "HMAC-SHA-256"


def test_hub_bootstrap_fails_closed_then_publishes_only_pinned_public_keyset(
    monkeypatch,
) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    missing = Flask("missing-projection-key")
    missing.secret_key = "test-secret-at-least-thirty-two-bytes"
    first = initialize_sfu_broadcast_hub_composition(missing)
    assert first.statuses["layer_projection_signing"].ready is False
    assert missing.extensions.get("sfu_layer_projection_service") is None

    private = Ed25519PrivateKey.generate()
    keyset = {
        "schema": "ananta.sfu-projection-trusted-keyset.v1",
        "keysetVersion": 1,
        "keys": [_key("hub-projection:v1", 1, "active", _public_raw(private))],
    }
    monkeypatch.setenv("ANANTA_SFU_PROJECTION_SIGNING_KEY_ID", "hub-projection:v1")
    monkeypatch.setenv("ANANTA_SFU_PROJECTION_SIGNING_KEY_VERSION", "1")
    monkeypatch.setenv(
        "ANANTA_SFU_PROJECTION_ED25519_PRIVATE_KEY_B64URL",
        _b64(_private_raw(private)),
    )
    monkeypatch.setenv("ANANTA_SFU_PROJECTION_TRUSTED_KEYSET_JSON", json.dumps(keyset))
    configured = Flask("configured-projection-key")
    configured.secret_key = "another-test-secret-at-least-thirty-two-bytes"
    second = initialize_sfu_broadcast_hub_composition(configured)

    assert second.statuses["layer_projection_signing"].ready is True
    assert configured.extensions["sfu_layer_projection_service"] is not None
    bootstrap = configured.extensions["sfu_projection_trusted_keyset_bootstrap"]
    assert bootstrap == keyset
    assert _b64(_private_raw(private)) not in json.dumps(bootstrap)
