from __future__ import annotations

import pytest

from agent.services.source_access_manifest_signing import (
    HubSourceAccessManifestSigner,
    SourceAccessManifestSigningError,
    SourceAccessSigningKey,
    WorkerSourceAccessManifestVerifier,
)


def test_hub_signature_is_verified_by_worker_without_policy_decision() -> None:
    key = SourceAccessSigningKey(
        key_id="source-access-2026-01",
        secret=b"a" * 32,
    )
    signer = HubSourceAccessManifestSigner(key)
    verifier = WorkerSourceAccessManifestVerifier(
        {key.key_id: b"a" * 32}
    )

    signature = signer.sign(manifest_digest="b" * 64)

    assert verifier.verify(
        manifest_digest="b" * 64,
        signature=signature,
    )
    assert not hasattr(verifier, "authorize")
    assert "<redacted>" in repr(key)
    assert "aaaa" not in repr(key)


def test_signature_is_bound_to_digest_and_key() -> None:
    signature = HubSourceAccessManifestSigner(
        SourceAccessSigningKey(key_id="key-one", secret=b"a" * 32)
    ).sign(manifest_digest="b" * 64)

    assert not WorkerSourceAccessManifestVerifier(
        {"key-one": b"a" * 32}
    ).verify(
        manifest_digest="c" * 64,
        signature=signature,
    )
    assert not WorkerSourceAccessManifestVerifier(
        {"key-one": b"z" * 32}
    ).verify(
        manifest_digest="b" * 64,
        signature=signature,
    )


def test_rotation_accepts_only_explicit_current_or_previous_keys() -> None:
    old = HubSourceAccessManifestSigner(
        SourceAccessSigningKey(key_id="old", secret=b"o" * 32)
    ).sign(manifest_digest="d" * 64)
    verifier = WorkerSourceAccessManifestVerifier(
        {"current": b"c" * 32, "old": b"o" * 32}
    )

    assert verifier.verify(manifest_digest="d" * 64, signature=old)
    assert not WorkerSourceAccessManifestVerifier(
        {"current": b"c" * 32}
    ).verify(
        manifest_digest="d" * 64,
        signature=old,
    )


def test_weak_or_malformed_key_is_rejected() -> None:
    with pytest.raises(SourceAccessManifestSigningError):
        SourceAccessSigningKey(key_id="../key", secret=b"a" * 32)
    with pytest.raises(SourceAccessManifestSigningError):
        SourceAccessSigningKey(key_id="key", secret=b"short")
