from __future__ import annotations

import json

import pytest

from agent.services.workflow_hub_task_gateway_runtime import (
    get_workflow_authorization_key_ring,
    reset_workflow_hub_task_gateway_service,
)
from agent.services.workflow_runtime.errors import SignatureValidationError
from agent.services.workflow_runtime.security import HmacKeyRing, RuntimeAuthorizationEnvelope


def _envelope(keys: HmacKeyRing, *, envelope_id: str) -> RuntimeAuthorizationEnvelope:
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


def test_production_keyring_applies_persisted_key_and_envelope_revocations(
    tmp_path, monkeypatch
) -> None:
    old_key = "o" * 32
    new_key = "n" * 32
    old_signer = HmacKeyRing({"old": old_key}, active_key_id="old")
    new_signer = HmacKeyRing({"new": new_key}, active_key_id="new")
    old_envelope = _envelope(old_signer, envelope_id="old-envelope")
    revoked_envelope = _envelope(new_signer, envelope_id="revoked-envelope")
    path = tmp_path / "workflow-auth-keyring.json"
    path.write_text(
        json.dumps(
            {
                "active_key_id": "new",
                "keys": {"old": old_key, "new": new_key},
                "revoked_key_ids": ["old"],
                "revoked_envelope_ids": ["revoked-envelope"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANANTA_WORKFLOW_AUTH_KEYRING_FILE", str(path))
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
