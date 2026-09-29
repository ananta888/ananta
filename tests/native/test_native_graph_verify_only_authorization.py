from __future__ import annotations

import base64
import json
import time

import pytest

from agent.services.workflow_runtime.security import RuntimeAuthorizationEnvelope
from ananta_contracts.runtime_authorization_crypto import Ed25519SigningKeyRing
from worker.runtime.native_graph.authorization import (
    HubBackedNativeAuthorizationVerifier,
    load_ed25519_native_authorization_verifier,
)


def _envelope() -> RuntimeAuthorizationEnvelope:
    now = time.time()
    return RuntimeAuthorizationEnvelope(
        envelope_id="envelope-1",
        tenant_id="tenant-1",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        plan_hash="plan-hash-1",
        policy_version="policy-v1",
        allowed_tools=("read_file",),
        allowed_artifacts=(),
        budgets={"attempts": 2, "timeout_seconds": 30},
        issued_at=now - 1,
        expires_at=now + 60,
        nonce="nonce-1",
        key_id="hub-public-key-1",
        signature="detached-signature",
    )


def _authorize(
    verifier: HubBackedNativeAuthorizationVerifier,
    envelope: RuntimeAuthorizationEnvelope,
    *,
    revalidator,
) -> None:
    verifier.authorize(
        envelope,
        tenant_id="tenant-1",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        plan_hash="plan-hash-1",
        policy_version="policy-v1",
        requested_budget={"attempts": 1, "timeout_seconds": 10},
        consume_nonce=True,
        hub_revalidator=revalidator,
        now=time.time(),
    )


def test_verify_only_worker_authority_requires_hub_verification() -> None:
    verifier = HubBackedNativeAuthorizationVerifier()
    calls: list[str] = []

    _authorize(
        verifier,
        _envelope(),
        revalidator=lambda envelope: calls.append(envelope.envelope_id) or True,
    )

    assert calls == ["envelope-1"]
    assert not hasattr(verifier, "sign")


def test_verify_only_worker_authority_rejects_hub_denial_and_replay() -> None:
    denied = HubBackedNativeAuthorizationVerifier()
    with pytest.raises(ValueError, match="authorization_hub_verification_failed"):
        _authorize(denied, _envelope(), revalidator=lambda _envelope: False)

    verifier = HubBackedNativeAuthorizationVerifier()
    envelope = _envelope()
    _authorize(verifier, envelope, revalidator=lambda _envelope: True)
    with pytest.raises(ValueError, match="authorization_replay_detected"):
        _authorize(verifier, envelope, revalidator=lambda _envelope: True)


def test_worker_loads_public_ed25519_verifier_without_signing_capability(
    tmp_path,
) -> None:
    signer = Ed25519SigningKeyRing(
        {"key-1": base64.b64encode(b"k" * 32)},
        active_key_id="key-1",
    )
    path = tmp_path / "workflow-verification-keyring.json"
    path.write_text(
        json.dumps(signer.verification_mapping(), sort_keys=True),
        encoding="utf-8",
    )
    path.chmod(0o600)
    verifier = load_ed25519_native_authorization_verifier(
        {"ANANTA_WORKFLOW_AUTH_VERIFICATION_KEYRING_FILE": str(path)}
    )
    assert verifier is not None
    assert not hasattr(verifier, "sign")
    now = time.time()
    envelope = RuntimeAuthorizationEnvelope.issue(
        key_ring=signer,
        tenant_id="tenant-1",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        plan_hash="plan-hash-1",
        policy_version="policy-v1",
        budgets={"attempts": 2},
        now=now,
    )

    verifier.authorize(
        envelope,
        tenant_id="tenant-1",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        plan_hash="plan-hash-1",
        policy_version="policy-v1",
        requested_budget={"attempts": 1},
        now=now + 1,
    )


def test_worker_public_key_loader_rejects_private_key_fields(tmp_path) -> None:
    signer = Ed25519SigningKeyRing(
        {"key-1": base64.b64encode(b"p" * 32)},
        active_key_id="key-1",
    )
    payload = signer.verification_mapping()
    payload["private_keys"] = {"key-1": "forbidden"}
    path = tmp_path / "invalid-verification-keyring.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(ValueError, match="authorization_keyring_unknown_field"):
        load_ed25519_native_authorization_verifier(
            {"ANANTA_WORKFLOW_AUTH_VERIFICATION_KEYRING_FILE": str(path)}
        )
