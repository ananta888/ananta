from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.workflow_runtime import (
    AuthorizationVerifier,
    ContractValidationError,
    HmacKeyRing,
    InMemoryReplayNonceStore,
    QuarantinedContract,
    RuntimeAuthorizationEnvelope,
    SignatureValidationError,
    SignedCheckpoint,
    UpcasterRegistry,
    WorkflowState,
)
from ananta_contracts.provider_execution import (
    ProviderBindingAuthorization,
    ProviderExecutionBinding,
    ProviderProfileAttemptPlanEntry,
    ProviderProfileExecutionBinding,
)


def _keys() -> HmacKeyRing:
    return HmacKeyRing({"key-1": "a" * 32}, active_key_id="key-1")


def _envelope(keys: HmacKeyRing, *, now: float = 100.0) -> RuntimeAuthorizationEnvelope:
    return RuntimeAuthorizationEnvelope.issue(
        key_ring=keys,
        tenant_id="tenant-a",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        plan_hash="f" * 64,
        policy_version="policy-2",
        allowed_tools=("git",),
        allowed_artifacts=("patch",),
        budgets={"tokens": 1000, "cost_micros": 500},
        ttl_seconds=60,
        now=now,
        nonce="nonce-1",
    )


def _authorize(verifier: AuthorizationVerifier, envelope: RuntimeAuthorizationEnvelope, **extra) -> None:
    now = extra.pop("now", 120)
    verifier.authorize(
        envelope,
        tenant_id="tenant-a",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        plan_hash="f" * 64,
        policy_version="policy-2",
        tool="git",
        requested_budget={"tokens": 500},
        now=now,
        **extra,
    )


def test_authorization_envelope_is_bound_signed_and_replay_protected() -> None:
    keys = _keys()
    replay = InMemoryReplayNonceStore(clock=lambda: 120)
    verifier = AuthorizationVerifier(keys, replay)
    envelope = _envelope(keys)

    _authorize(verifier, envelope, consume_nonce=True)

    with pytest.raises(SignatureValidationError, match="replay"):
        _authorize(verifier, envelope, consume_nonce=True)
    with pytest.raises(SignatureValidationError, match="tenant_id_mismatch"):
        envelope.verify(
            key_ring=keys,
            tenant_id="tenant-b",
            workflow_id="workflow-1",
            run_id="run-1",
            step_id="step-1",
            plan_hash="f" * 64,
            policy_version="policy-2",
            now=120,
        )
    with pytest.raises(SignatureValidationError, match="expired"):
        _authorize(verifier, envelope, now=200)


def test_authorization_tamper_rotation_revocation_and_write_revalidation_fail_closed() -> None:
    keys = _keys()
    envelope = _envelope(keys)
    tampered = replace(envelope, allowed_tools=("shell",))

    with pytest.raises(SignatureValidationError, match="signature_invalid"):
        tampered.verify(
            key_ring=keys,
            tenant_id="tenant-a",
            workflow_id="workflow-1",
            run_id="run-1",
            step_id="step-1",
            plan_hash="f" * 64,
            policy_version="policy-2",
            now=120,
        )

    keys.rotate(key_id="key-2", key="b" * 32)
    _authorize(AuthorizationVerifier(keys), envelope)
    keys.revoke_key("key-1")
    with pytest.raises(SignatureValidationError, match="revoked"):
        _authorize(AuthorizationVerifier(keys), envelope)

    fresh = _envelope(keys)
    with pytest.raises(SignatureValidationError, match="hub_revalidation"):
        _authorize(AuthorizationVerifier(keys), fresh, writing=True)
    _authorize(AuthorizationVerifier(keys), fresh, writing=True, hub_revalidator=lambda _: True)


def test_provider_authorization_is_signed_and_empty_extension_preserves_old_envelope() -> None:
    keys = _keys()
    legacy = _envelope(keys)
    legacy_payload = legacy.to_dict()
    assert "allowed_provider_bindings" not in legacy_payload
    assert "provider_attempt_plan" not in legacy_payload
    _authorize(
        AuthorizationVerifier(keys),
        RuntimeAuthorizationEnvelope.from_mapping(legacy_payload),
    )

    execution_binding = ProviderExecutionBinding(
        provider_id="ollama",
        model_id="phi4-mini:latest",
        source="hub_model_profile_routing",
        reason_code="hub_provider_profile_selected",
        endpoint_identity=(
            "http://ollama:11434/v1/chat/completions"
        ),
    )
    profile_binding = ProviderProfileExecutionBinding(
        profile_id="phi-primary",
        binding=execution_binding,
    )
    authorization = ProviderBindingAuthorization.from_binding(
        execution_binding
    )
    plan_entry = ProviderProfileAttemptPlanEntry.from_profile_binding(
        profile_binding,
        maximum_attempts=3,
    )
    envelope = RuntimeAuthorizationEnvelope.issue(
        key_ring=keys,
        tenant_id="tenant-a",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        plan_hash="f" * 64,
        policy_version="policy-2",
        allowed_provider_bindings=(authorization,),
        provider_attempt_plan=(plan_entry,),
        budgets={"provider_attempts": 3, "tokens": 1000},
        ttl_seconds=60,
        now=100,
    )
    envelope.verify(
        key_ring=keys,
        tenant_id="tenant-a",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        plan_hash="f" * 64,
        policy_version="policy-2",
        now=120,
    )

    tampered_model = "gemma4:e4b-it-qat"
    tampered = replace(
        envelope,
        allowed_provider_bindings=(
            replace(authorization, model_id=tampered_model),
        ),
        provider_attempt_plan=(
            replace(plan_entry, model_id=tampered_model),
        ),
    )
    with pytest.raises(SignatureValidationError, match="signature_invalid"):
        tampered.verify(
            key_ring=keys,
            tenant_id="tenant-a",
            workflow_id="workflow-1",
            run_id="run-1",
            step_id="step-1",
            plan_hash="f" * 64,
            policy_version="policy-2",
            now=120,
        )

    tampered_endpoint = (
        "http://ollama:11435/v1/chat/completions"
    )
    endpoint_tampered = replace(
        envelope,
        allowed_provider_bindings=(
            replace(
                authorization,
                endpoint_identity=tampered_endpoint,
            ),
        ),
        provider_attempt_plan=(
            replace(
                plan_entry,
                endpoint_identity=tampered_endpoint,
            ),
        ),
    )
    with pytest.raises(SignatureValidationError, match="signature_invalid"):
        endpoint_tampered.verify(
            key_ring=keys,
            tenant_id="tenant-a",
            workflow_id="workflow-1",
            run_id="run-1",
            step_id="step-1",
            plan_hash="f" * 64,
            policy_version="policy-2",
            now=120,
        )

    legacy_binding = ProviderExecutionBinding(
        provider_id="ollama",
        model_id="phi4-mini:latest",
        source="hub_model_profile_routing",
        reason_code="hub_provider_profile_selected",
    )
    assert "endpoint_identity" not in legacy_binding.to_dict()
    assert legacy_binding.binding_id != execution_binding.binding_id


def test_state_and_checkpoint_reject_secrets_tamper_cross_run_and_stale_fence() -> None:
    keys = _keys()
    with pytest.raises(ContractValidationError, match="embedded_secret"):
        WorkflowState(business_data={"password": "raw"}).assert_safe()

    state = WorkflowState(
        business_data={"answer": 42},
        runtime_metadata={"cursor": 3},
        secret_refs=("vault://runtime/key",),
        artifact_refs=("artifact://result",),
    )
    checkpoint = SignedCheckpoint.issue(
        key_ring=keys,
        tenant_id="tenant-a",
        workflow_id="workflow-1",
        run_id="run-1",
        task_id="task-1",
        plan_hash="f" * 64,
        policy_version="policy-2",
        runtime_id="native",
        runtime_version="1",
        state=state,
        revision=1,
        fencing_token=4,
        now=100,
    )
    checkpoint.verify(
        key_ring=keys,
        tenant_id="tenant-a",
        workflow_id="workflow-1",
        run_id="run-1",
        task_id="task-1",
        plan_hash="f" * 64,
        policy_version="policy-2",
        min_fencing_token=4,
    )
    with pytest.raises(SignatureValidationError, match="run_id_mismatch"):
        checkpoint.verify(
            key_ring=keys,
            tenant_id="tenant-a",
            workflow_id="workflow-1",
            run_id="run-2",
            task_id="task-1",
            plan_hash="f" * 64,
            policy_version="policy-2",
        )
    with pytest.raises(SignatureValidationError, match="stale"):
        checkpoint.verify(
            key_ring=keys,
            tenant_id="tenant-a",
            workflow_id="workflow-1",
            run_id="run-1",
            task_id="task-1",
            plan_hash="f" * 64,
            policy_version="policy-2",
            min_fencing_token=5,
        )
    with pytest.raises(SignatureValidationError, match="signature_invalid"):
        replace(checkpoint, state=replace(state, business_data={"answer": 43})).verify(
            key_ring=keys,
            tenant_id="tenant-a",
            workflow_id="workflow-1",
            run_id="run-1",
            task_id="task-1",
            plan_hash="f" * 64,
            policy_version="policy-2",
        )


def test_upcaster_is_deterministic_and_unknown_versions_are_quarantined() -> None:
    registry = UpcasterRegistry()
    registry.register(
        contract_type="event",
        source_schema="ananta.workflow_event.v0",
        target_schema="ananta.workflow_event.v1",
        upcaster=lambda raw: {**raw, "schema": "ananta.workflow_event.v1", "attempt": raw.get("attempt", 0)},
    )
    original = {"schema": "ananta.workflow_event.v0", "event_type": "started"}
    migrated = registry.upcast(
        original,
        contract_type="event",
        target_schema="ananta.workflow_event.v1",
    )

    assert original["schema"] == "ananta.workflow_event.v0"
    assert migrated["attempt"] == 0
    assert registry.migration_path(
        contract_type="event",
        source_schema="ananta.workflow_event.v0",
        target_schema="ananta.workflow_event.v1",
    ) == ("ananta.workflow_event.v0", "ananta.workflow_event.v1")

    quarantined = registry.upcast_or_quarantine(
        {"schema": "ananta.workflow_event.v99"},
        contract_type="event",
        target_schema="ananta.workflow_event.v1",
    )
    assert isinstance(quarantined, QuarantinedContract)
    assert quarantined.source_schema == "ananta.workflow_event.v99"

    secret_quarantine = registry.upcast_or_quarantine(
        {"schema": "ananta.workflow_event.v99", "api_token": "raw-secret"},
        contract_type="event",
        target_schema="ananta.workflow_event.v1",
    )
    assert isinstance(secret_quarantine, QuarantinedContract)
    assert secret_quarantine.payload["api_token"] == "[REDACTED]"
