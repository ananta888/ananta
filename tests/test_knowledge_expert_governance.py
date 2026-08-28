from __future__ import annotations

import base64
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent.services.knowledge_augmentation_adapters import KnowledgeAugmentationAdapters
from agent.services.knowledge_augmentation_policy_service import KnowledgeAugmentationPolicyService
from agent.services.knowledge_expert_activation_service import KnowledgeExpertActivationService
from agent.services.knowledge_expert_control_service import KnowledgeExpertControlService
from agent.services.knowledge_expert_evidence_service import KnowledgeExpertEvidenceService
from agent.services.knowledge_expert_observer import KnowledgeExpertObserver
from agent.services.knowledge_expert_promotion_gate import KnowledgeExpertPromotionGate
from agent.services.knowledge_expert_refresh_planner import KnowledgeExpertRefreshPlanner
from agent.services.knowledge_expert_registry_service import (
    InMemoryKnowledgeExpertRegistryStore,
    KnowledgeExpertRegistryService,
)
from agent.services.knowledge_expert_rollout_service import KnowledgeExpertRolloutService
from agent.services.knowledge_expert_security_policy import KnowledgeExpertSecurityPolicy
from agent.services.knowledge_expert_signing_service import KnowledgeExpertSigningService
from agent.services.knowledge_expert_task_service import KnowledgeExpertTaskService
from ananta_contracts.parametric_knowledge import (
    ParametricKnowledgeUnit,
    canonical_sha256,
    knowledge_unit_set_digest,
)
from ananta_contracts.runtime_authorization_crypto import Ed25519SigningKeyRing
from worker.inference.knowledge_expert_signature_verifier import KnowledgeExpertSignatureVerifier
from worker.training.knowledge_expert_job_handler import KnowledgeExpertJobHandler

DIGEST = "a" * 64
ROOT = Path(__file__).resolve().parents[1]


def _unit(**overrides) -> ParametricKnowledgeUnit:
    raw = {
        "schema": "ananta.parametric-knowledge-unit.v1",
        "unit_id": "unit-1",
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "repository_id": "repo-1",
        "source_id": "SRC_0001",
        "source_revision": "rev-1",
        "content_hash": DIGEST,
        "provenance_digest": "b" * 64,
        "domain": "payments",
        "parent_id": "",
        "relations": [],
        "sensitivity": "public",
        "retention_until": "2099-01-01T00:00:00Z",
        "license_spdx": "MIT",
        "citation_ref": "SRC_0001",
        "citation_required": False,
        "stable": True,
        "approval_state": "approved",
        "revoked": False,
    }
    raw.update(overrides)
    return ParametricKnowledgeUnit.from_mapping(raw)


def _signing():
    signer = Ed25519SigningKeyRing({"key-1": base64.b64encode(b"k" * 32).decode()}, active_key_id="key-1")
    return KnowledgeExpertSigningService(signer), KnowledgeExpertSignatureVerifier(signer.verification_key_ring())


def _manifest(signing: KnowledgeExpertSigningService, *, unit: ParametricKnowledgeUnit | None = None):
    bound_unit = unit or _unit()
    unit_digest = knowledge_unit_set_digest([bound_unit])
    return signing.sign_manifest(
        {
            "schema": "ananta.knowledge-expert-manifest.v1",
            "expert_id": "expert-1",
            "generation_id": "generation-1",
            "tenant_id": bound_unit.tenant_id,
            "workspace_id": bound_unit.workspace_id,
            "repository_id": bound_unit.repository_id,
            "knowledge_unit_ids": [bound_unit.unit_id],
            "knowledge_unit_digest": unit_digest,
            "adapter_format": "safetensors",
            "adapter_digest": "d" * 64,
            "adapter_size_bytes": 1024,
            "compatibility": {
                "base_model_digest": "e" * 64,
                "tokenizer_digest": "f" * 64,
                "architecture": "llama",
                "target_layer": "final_ffn",
                "target_modules": ["down_proj"],
                "runtime_provider": "prototype",
                "runtime_version": "1",
                "kv_cache_safe": True,
            },
            "peft_configuration_digest": "1" * 64,
            "training_dataset_digest": "2" * 64,
            "evaluation_status": "passed",
            "evaluation_digest": "3" * 64,
            "policy_decision_digest": "4" * 64,
        }
    )


def test_signed_registry_is_immutable_scope_bound_and_cas_activated():
    signing, verifier = _signing()
    manifest = _manifest(signing)
    bank = signing.sign_bank(
        {
            "schema": "ananta.knowledge-expert-bank.v1",
            "bank_id": "bank-1",
            "generation_id": "generation-1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "repository_id": "repo-1",
            "previous_generation_id": "",
            "expert_manifest_digests": [manifest.manifest_digest],
            "status": "admitted",
            "policy_digest": "5" * 64,
            "created_at": "2026-08-27T00:00:00Z",
        }
    )
    store = InMemoryKnowledgeExpertRegistryStore()

    class _Invalidator:
        digests = []

        def invalidate_manifest(self, *, manifest_digest):
            self.digests.append(manifest_digest)

    invalidator = _Invalidator()

    class _Units:
        def get(self, *, unit_id):
            return _unit() if unit_id == "unit-1" else None

    registry = KnowledgeExpertRegistryService(
        store=store,
        verifier=verifier,
        promotion_gate=KnowledgeExpertPromotionGate(),
        cache_invalidator=invalidator,
        unit_catalog=_Units(),
    )
    registry.register_manifest(manifest)
    with pytest.raises(ValueError, match="signature_invalid"):
        registry.register_manifest(replace(manifest, adapter_digest="9" * 64))
    invalid_unit_binding = signing.sign_manifest(
        {**manifest.to_dict(), "knowledge_unit_digest": "9" * 64, "signature": ""}
    )
    with pytest.raises(ValueError, match="unit_digest_mismatch"):
        registry.register_manifest(invalid_unit_binding)
    registry.register_bank(
        bank,
        promotion_evidence={
            "schema": "ananta.knowledge-expert-promotion-evidence.v1",
            "bank_digest": bank.bank_digest,
            "compositions": [{"manifest_digests": [manifest.manifest_digest], "passed": True}],
            "general_holdout_passed": True,
            "security_holdout_passed": True,
        },
    )
    assert (
        registry.activate(bank_id="bank-1", generation_id="generation-1", expected_active_generation="")[
            "active_generation_id"
        ]
        == "generation-1"
    )
    with pytest.raises(ValueError, match="activation_conflict"):
        registry.activate(bank_id="bank-1", generation_id="generation-1", expected_active_generation="")
    registry.revoke_manifest(manifest.manifest_digest)
    assert invalidator.digests == [manifest.manifest_digest]
    with pytest.raises(ValueError, match="revoked"):
        registry.activate(bank_id="bank-1", generation_id="generation-1", expected_active_generation="generation-1")


class _Evidence:
    def __init__(self, source_id="SRC_0001"):
        self.source_id = source_id

    def resolve(self, *, source_ids, query):
        return [{"source_id": self.source_id, "revision": "rev-1"}]


def test_evidence_never_accepts_an_unprovided_source_identifier():
    signing, _ = _signing()
    manifest = _manifest(signing)
    with pytest.raises(ValueError, match="unknown_source"):
        KnowledgeExpertEvidenceService(citation_evidence=_Evidence("unknown-source")).build(
            manifest=manifest, units=[_unit()], query="why", citation_required=True
        )
    with pytest.raises(ValueError, match="unknown_source"):
        revised = _unit(source_revision="rev-2")
        KnowledgeExpertEvidenceService(citation_evidence=_Evidence()).build(
            manifest=_manifest(signing, unit=revised), units=[revised], query="why", citation_required=True
        )


def test_hybrid_policy_keeps_rag_for_citation_and_runtime_failure():
    policy = KnowledgeAugmentationPolicyService(profiles={"default": {"mode": "auto"}}, policy_digest="a" * 64)
    base = dict(
        profile_id="default",
        global_enabled=True,
        model_enabled=True,
        task_enabled=True,
        domain_enabled=True,
        data_class_enabled=True,
        expert_selected=True,
        rag_available=True,
    )
    citation = policy.decide(**base, runtime_ready=True, citation_required=True)
    unavailable = policy.decide(**base, runtime_ready=False, citation_required=False)
    assert citation.mode == "expert_plus_rag"
    assert unavailable.mode == "rag_only"


def test_all_transport_adapters_share_policy_and_deny_client_authority():
    adapters = KnowledgeAugmentationAdapters(
        KnowledgeAugmentationPolicyService(profiles={"default": {"mode": "auto"}}, policy_digest="a" * 64)
    )
    request = {"schema": "ananta.knowledge-augmentation-request.v1", "profile_id": "default"}
    context = {
        "global_enabled": True,
        "model_enabled": True,
        "task_enabled": True,
        "domain_enabled": True,
        "data_class_enabled": True,
        "runtime_ready": True,
        "expert_selected": True,
        "citation_required": False,
        "rag_available": True,
    }
    decisions = [
        adapters.for_agent(request, hub_context=context),
        adapters.for_mcp(request, hub_context=context),
        adapters.for_http(request, hub_context=context),
        adapters.for_n8n(request, hub_context=context),
    ]
    assert decisions.count(decisions[0]) == 4
    with pytest.raises(ValueError, match="client_authority_denied"):
        adapters.for_mcp({**request, "expert_id": "expert-1"}, hub_context=context)


def test_refresh_plan_is_deterministic_and_propagates_dependency_review():
    previous = [_unit(), _unit(unit_id="unit-2", content_hash="2" * 64)]
    current = [_unit(content_hash="3" * 64), _unit(unit_id="unit-2", content_hash="2" * 64)]
    planner = KnowledgeExpertRefreshPlanner()
    inputs = {
        "previous": previous,
        "current": current,
        "dependency_edges": {"unit-2": ["unit-1"]},
        "unit_manifest_bindings": {"unit-1": ["a" * 64]},
    }
    first = planner.plan(**inputs)
    second = planner.plan(**inputs)
    assert first == second
    assert first.retrain == ("unit-1",)
    assert first.review == ("unit-2",)
    assert first.revoke_manifest_digests == ("a" * 64,)


class _Queue:
    def __init__(self):
        self.tasks = []
        self.cancelled = False

    def enqueue(self, task):
        self.tasks.append(task)

    def is_cancelled(self, *, task_id, attempt_id):
        return self.cancelled


class _Job:
    def execute(self, task, payload):
        return {"artifact_digest": "f" * 64}


def test_task_replay_audience_input_and_cancellation_fail_before_activation():
    queue = _Queue()
    service = KnowledgeExpertTaskService(queue=queue, clock=lambda: datetime(2026, 8, 27, tzinfo=timezone.utc))
    payload: dict = {}
    payload_digest = canonical_sha256(payload)
    task = service.submit(
        task_id="task-1",
        attempt_id="attempt-1",
        task_type="train_expert",
        worker_audience="worker-1",
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        repository_id="repo-1",
        input_digest=payload_digest,
        policy_digest="b" * 64,
    )
    handler = KnowledgeExpertJobHandler(worker_audience="worker-1", handlers={"train_expert": _Job()})
    result = handler.execute(
        task,
        current_attempt_id="attempt-1",
        payload=payload,
        payload_digest=payload_digest,
        now=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    assert result["activation_authorized"] is False
    with pytest.raises(ValueError, match="stale_attempt"):
        handler.execute(
            task,
            current_attempt_id="attempt-2",
            payload=payload,
            payload_digest=payload_digest,
            now=datetime(2026, 8, 27, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="input_digest_mismatch"):
        handler.execute(
            task,
            current_attempt_id="attempt-1",
            payload={"tampered": True},
            payload_digest=payload_digest,
            now=datetime(2026, 8, 27, tzinfo=timezone.utc),
        )
    queue.cancelled = True
    with pytest.raises(ValueError, match="cancelled"):
        service.assert_result_current(task, attempt_id="attempt-1")

    class _Registry:
        called = False

        def activate(self, **kwargs):
            self.called = True
            return kwargs

    registry = _Registry()
    activation = KnowledgeExpertActivationService(tasks=service, registry=registry)
    publish = replace(task, task_type="publish_bank")
    with pytest.raises(ValueError, match="cancelled"):
        activation.activate_published_bank(
            task=publish,
            result_attempt_id="attempt-1",
            bank_id="bank-1",
            generation_id="generation-1",
            expected_active_generation="",
        )
    assert registry.called is False


class _Control:
    def snapshot(self, *, tenant_id):
        return {"rollout_state": "off", "gates": {"research": "blocked"}}

    def command(self, **kwargs):
        return {"reason_code": "task_queued", "task_id": "task-1"}


def test_control_service_is_default_off_and_never_grants_worker_activation():
    disabled = KnowledgeExpertControlService(_Control(), enabled=False)
    assert disabled.snapshot(tenant_id="tenant-1")["fallback_mode"] == "rag_only"
    with pytest.raises(ValueError, match="control_disabled"):
        disabled.command(
            tenant_id="tenant-1",
            action="activate",
            bank_id="bank-1",
            generation_id="g2",
            expected_generation_id="g1",
            reason="approved rollout",
        )
    enabled = KnowledgeExpertControlService(_Control(), enabled=True)
    result = enabled.command(
        tenant_id="tenant-1",
        action="rollback",
        bank_id="bank-1",
        generation_id="g1",
        expected_generation_id="g2",
        reason="restore last good",
    )
    assert result["activation_performed_by_worker"] is False


def test_security_and_observability_do_not_relax_scope_or_log_raw_ids():
    signing, _ = _signing()
    manifest = _manifest(signing)
    decision = KnowledgeExpertSecurityPolicy().evaluate(
        manifest,
        scope={"tenant_id": "other", "workspace_id": "workspace-1", "repository_id": "repo-1"},
        signature_verified=True,
        poisoning_gate_passed=True,
        backdoor_gate_passed=True,
        extraction_risk_accepted=True,
    )
    assert decision.reason_code == "expert_scope_mismatch"

    class _Sink:
        event = None

        def emit(self, event):
            self.event = dict(event)

    sink = _Sink()
    KnowledgeExpertObserver(sink, id_salt=b"0123456789abcdef").record(
        event_type="fallback",
        reason_code="scope_denied",
        tenant_id="tenant-secret",
        mode="rag_only",
        duration_ms=4,
        expert_count=0,
        input_tokens=10,
        output_tokens=2,
    )
    assert "tenant-secret" not in str(sink.event)
    assert sink.event["tenant_bucket"]


def test_rollout_is_default_off_and_ga_is_blocked_by_unproven_gates():
    config = json.loads((ROOT / "config/knowledge-experts/rollout.v1.json").read_text())
    rollout = KnowledgeExpertRolloutService(config)
    assert rollout.routing_enabled(tenant_id="tenant-1") is False
    assert rollout.snapshot()["fallback_mode"] == "rag_only"
    with pytest.raises(ValueError, match="gates_blocked"):
        rollout.assert_stage_allowed("ga")
