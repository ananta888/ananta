from __future__ import annotations

import base64
import json

import pytest

from agent.services.workflow_hub_task_gateway_runtime import (
    WorkflowHubTaskConfigurationError,
    get_workflow_authorization_key_ring,
    reset_workflow_hub_task_gateway_service,
)
from agent.services.workflow_runtime.errors import SignatureValidationError
from agent.services.workflow_runtime.security import RuntimeAuthorizationEnvelope
from ananta_contracts.runtime_authorization_crypto import (
    ED25519_ALGORITHM,
    ED25519_SIGNING_KEYRING_SCHEMA,
    Ed25519SigningKeyRing,
)


def _envelope(keys, *, envelope_id: str) -> RuntimeAuthorizationEnvelope:
    return RuntimeAuthorizationEnvelope.issue(
        key_ring=keys,
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        step_id="step-a",
        plan_hash="a" * 64,
        policy_version="policy-v1",
        now=100,
        ttl_seconds=1000,
        envelope_id=envelope_id,
        nonce=f"nonce-{envelope_id}",
    )


def test_production_keyring_applies_persisted_key_and_envelope_revocations(tmp_path, monkeypatch) -> None:
    old_key = base64.b64encode(b"o" * 32).decode("ascii")
    new_key = base64.b64encode(b"n" * 32).decode("ascii")
    old_signer = Ed25519SigningKeyRing({"old": old_key}, active_key_id="old")
    new_signer = Ed25519SigningKeyRing({"new": new_key}, active_key_id="new")
    old_envelope = _envelope(old_signer, envelope_id="old-envelope")
    revoked_envelope = _envelope(new_signer, envelope_id="revoked-envelope")
    path = tmp_path / "workflow-auth-keyring.json"
    path.write_text(
        json.dumps(
            {
                "schema": ED25519_SIGNING_KEYRING_SCHEMA,
                "algorithm": ED25519_ALGORITHM,
                "active_key_id": "new",
                "private_keys": {"old": old_key, "new": new_key},
                "revoked_key_ids": ["old"],
                "revoked_envelope_ids": ["revoked-envelope"],
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o444)
    monkeypatch.setenv("ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_FILE", str(path))
    reset_workflow_hub_task_gateway_service()

    production = get_workflow_authorization_key_ring()

    with pytest.raises(SignatureValidationError, match="signing_key_revoked"):
        old_envelope.verify(
            key_ring=production,
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            run_id="run-a",
            step_id="step-a",
            plan_hash="a" * 64,
            policy_version="policy-v1",
            now=200,
        )
    with pytest.raises(SignatureValidationError, match="signed_contract_revoked"):
        revoked_envelope.verify(
            key_ring=production,
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            run_id="run-a",
            step_id="step-a",
            plan_hash="a" * 64,
            policy_version="policy-v1",
            now=200,
        )
    reset_workflow_hub_task_gateway_service()


def test_production_hub_rejects_symlinked_signing_keyring(
    tmp_path,
    monkeypatch,
) -> None:
    private_key = base64.b64encode(b"s" * 32).decode("ascii")
    target = tmp_path / "target.json"
    target.write_text(
        json.dumps(
            {
                "schema": ED25519_SIGNING_KEYRING_SCHEMA,
                "algorithm": ED25519_ALGORITHM,
                "active_key_id": "signer",
                "private_keys": {"signer": private_key},
            }
        ),
        encoding="utf-8",
    )
    link = tmp_path / "signing-keyring.json"
    link.symlink_to(target)
    monkeypatch.setenv("ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_FILE", str(link))
    reset_workflow_hub_task_gateway_service()

    with pytest.raises(
        WorkflowHubTaskConfigurationError,
        match="file cannot be read",
    ):
        get_workflow_authorization_key_ring()
    reset_workflow_hub_task_gateway_service()


def test_production_hub_rejects_group_or_world_writable_signing_keyring(
    tmp_path,
    monkeypatch,
) -> None:
    private_key = base64.b64encode(b"u" * 32).decode("ascii")
    path = tmp_path / "unsafe-signing-keyring.json"
    path.write_text(
        json.dumps(
            {
                "schema": ED25519_SIGNING_KEYRING_SCHEMA,
                "algorithm": ED25519_ALGORITHM,
                "active_key_id": "signer",
                "private_keys": {"signer": private_key},
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o666)
    monkeypatch.setenv("ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_FILE", str(path))
    reset_workflow_hub_task_gateway_service()

    with pytest.raises(
        WorkflowHubTaskConfigurationError,
        match="file is unsafe",
    ):
        get_workflow_authorization_key_ring()
    reset_workflow_hub_task_gateway_service()


def test_production_hub_rejects_oversized_signing_keyring(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "oversized-signing-keyring.json"
    path.write_bytes(b"{" + b" " * 65_536 + b"}")
    path.chmod(0o600)
    monkeypatch.setenv("ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_FILE", str(path))
    reset_workflow_hub_task_gateway_service()

    with pytest.raises(
        WorkflowHubTaskConfigurationError,
        match="file is invalid",
    ):
        get_workflow_authorization_key_ring()
    reset_workflow_hub_task_gateway_service()


def test_production_hub_rejects_legacy_shared_hmac_without_explicit_dev_flag(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "legacy-auth-keyring.json"
    path.write_text(
        json.dumps(
            {
                "active_key_id": "legacy",
                "keys": {"legacy": "x" * 32},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_FILE", raising=False)
    monkeypatch.setenv("ANANTA_WORKFLOW_AUTH_KEYRING_FILE", str(path))
    monkeypatch.delenv("ANANTA_WORKFLOW_ALLOW_LEGACY_HMAC_KEYRING", raising=False)
    reset_workflow_hub_task_gateway_service()

    with pytest.raises(
        RuntimeError,
        match="workflow_authorization_ed25519_signing_keyring_required",
    ):
        get_workflow_authorization_key_ring()
    reset_workflow_hub_task_gateway_service()
