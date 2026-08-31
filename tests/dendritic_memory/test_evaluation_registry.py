from __future__ import annotations

import pytest

from agent.services.dendritic_memory_evaluation_attestation import DendriticMemoryEvaluationAttestation
from agent.services.dendritic_memory_evaluation_service import DendriticMemoryEvaluationService
from agent.services.dendritic_memory_policy import DendriticMemoryPolicy
from agent.services.dendritic_memory_registry_service import (
    DendriticMemoryRegistryConflict,
    DendriticMemoryRegistryService,
)
from agent.services.dendritic_memory_runtime_gate import DendriticMemoryRuntimeGate
from tests.dendritic_memory.helpers import evaluation_input, leakage, pack


def _evaluation(service, pack_digest, *, canary=0):
    return service.compare(
        baseline=evaluation_input(accuracy=0.5, loss=1.0),
        lora=evaluation_input(accuracy=0.6, loss=0.8),
        dendritic=evaluation_input(accuracy=0.7, loss=0.7, pack_digest=pack_digest),
        leakage=leakage(canary=canary),
    )


def _registry(tmp_path, policy=None):
    policy = policy or DendriticMemoryPolicy()
    attestations = DendriticMemoryEvaluationAttestation(b"e" * 32)
    runtime_gate = DendriticMemoryRuntimeGate(
        policy=policy, evaluations=attestations, signing_key=b"r" * 32
    )
    registry = DendriticMemoryRegistryService(
        tmp_path / "registry.sqlite3",
        policy=policy,
        attestations=attestations,
        runtime_gate=runtime_gate,
    )
    return registry, attestations, runtime_gate


def test_evaluation_fails_closed_on_leakage() -> None:
    service = DendriticMemoryEvaluationService(DendriticMemoryEvaluationAttestation(b"e" * 32))
    result = _evaluation(service, "f" * 64, canary=1)
    assert result["experiment_eligible"] is False
    assert "dendritic_leakage_gate_failed" in result["reason_codes"]


def test_evaluation_exports_comparable_splits_resources_and_provenance() -> None:
    service = DendriticMemoryEvaluationService(DendriticMemoryEvaluationAttestation(b"e" * 32))
    result = _evaluation(service, "f" * 64)
    assert result["metric_definitions"]["accuracy"]["direction"] == "higher_is_better"
    assert result["benchmark_groups"] == ["trained", "transfer", "negative_control", "leakage"]
    assert result["resources"]["dendritic"]["host_ram_bytes"] == 1024
    assert result["provenance"]["dendritic"]["deterministic"] is True
    assert result["seed_aggregation"] == "multiple_seeds"


def test_continual_report_requires_disabled_single_multiple_and_unchanged_base() -> None:
    service = DendriticMemoryEvaluationService(DendriticMemoryEvaluationAttestation(b"e" * 32))
    common = {
        "forward_transfer": 0.1,
        "backward_transfer": 0.0,
        "forgetting": 0.01,
        "interference": 0.02,
        "base_model_before_digest": "b" * 64,
        "base_model_after_digest": "b" * 64,
    }
    report = service.continual_learning(
        runs=[
            {**common, "pack_order": [], "mode": "disabled", "seed": 1},
            {**common, "pack_order": ["c" * 64], "mode": "single", "seed": 2},
            {**common, "pack_order": ["c" * 64, "d" * 64], "mode": "multiple", "seed": 3},
        ]
    )
    assert report["seed_count"] == 3
    assert report["pack_orders"][-1] == ["c" * 64, "d" * 64]


def test_registry_requires_attested_pack_bound_evaluation(tmp_path) -> None:
    registry, attestations, _runtime_gate = _registry(tmp_path)
    manifest, _files = pack()
    registry.quarantine(
        manifest=manifest.to_dict(), artifact_ref="artifact:one", idempotency_key="quarantine-0001"
    )
    evaluation = _evaluation(DendriticMemoryEvaluationService(attestations), manifest.digest)
    approved = registry.approve_evaluated(
        tenant_id="tenant-1",
        pack_digest=manifest.digest,
        evaluation=evaluation,
        expected_revision=1,
        idempotency_key="approve-0001",
    )
    assert approved["state"] == "approved_for_experiment"
    tampered = {**evaluation, "experiment_eligible": False}
    with pytest.raises(PermissionError, match="attestation_invalid"):
        registry.approve_evaluated(
            tenant_id="tenant-1",
            pack_digest=manifest.digest,
            evaluation=tampered,
            expected_revision=2,
            idempotency_key="approve-0002",
        )
    revoked = registry.revoke(
        tenant_id="tenant-1",
        pack_digest=manifest.digest,
        expected_revision=2,
        idempotency_key="revoke-0001",
    )
    assert revoked["state"] == "revoked"


def test_runtime_activation_is_default_off_even_for_approved_pack(tmp_path) -> None:
    registry, attestations, runtime_gate = _registry(tmp_path)
    evaluation = _evaluation(DendriticMemoryEvaluationService(attestations), "f" * 64)
    receipt = runtime_gate.evaluate(
        pack_digest="f" * 64,
        base_model_snapshot_digest="b" * 64,
        evaluation=evaluation,
        capability={"state": "available", "available": True},
    )
    with pytest.raises(PermissionError, match="runtime_gate_failed"):
        registry.activate(
            tenant_id="tenant-1",
            scope_id="scope-1",
            pack_digest="f" * 64,
            expected_route_revision=0,
            gate_receipt=receipt,
            idempotency_key="activate-0001",
        )


def test_all_green_runtime_path_activates_deactivates_and_revokes_automatically(tmp_path) -> None:
    policy = DendriticMemoryPolicy(
        enabled=True,
        mode="mock",
        runtime_enabled=True,
        automatic_activation_enabled=True,
    )
    registry, attestations, runtime_gate = _registry(tmp_path, policy)
    manifest, _files = pack()
    registry.quarantine(
        manifest=manifest.to_dict(), artifact_ref="artifact:one", idempotency_key="quarantine-0001"
    )
    evaluation = _evaluation(DendriticMemoryEvaluationService(attestations), manifest.digest)
    registry.approve_evaluated(
        tenant_id="tenant-1",
        pack_digest=manifest.digest,
        evaluation=evaluation,
        expected_revision=1,
        idempotency_key="approve-0001",
    )
    receipt = runtime_gate.evaluate(
        pack_digest=manifest.digest,
        base_model_snapshot_digest=manifest.base_model_snapshot_digest,
        evaluation=evaluation,
        capability={"state": "available", "available": True},
    )
    active = registry.activate(
        tenant_id="tenant-1",
        scope_id="planning",
        pack_digest=manifest.digest,
        expected_route_revision=0,
        gate_receipt=receipt,
        idempotency_key="activate-0001",
    )
    assert active["active"] is True
    assert active["human_intervention_required"] is False
    inactive = registry.deactivate(
        tenant_id="tenant-1",
        scope_id="planning",
        expected_route_revision=1,
        idempotency_key="deactivate-0001",
    )
    assert inactive["active"] is False
    revoked = registry.revoke(
        tenant_id="tenant-1",
        pack_digest=manifest.digest,
        expected_revision=2,
        idempotency_key="revoke-0001",
    )
    assert revoked["state"] == "revoked"
    deleted = registry.delete(
        tenant_id="tenant-1",
        pack_digest=manifest.digest,
        expected_revision=revoked["revision"],
        idempotency_key="delete-0001",
    )
    assert deleted["state"] == "deleted"
    assert deleted["artifact_ref"] is None
    actions = {event["action"] for event in registry.audit(tenant_id="tenant-1")["items"]}
    assert {"import", "approve", "activate", "rollback", "revoke", "delete"} <= actions
    assert all(event["human_intervention_required"] is False for event in registry.audit(tenant_id="tenant-1")["items"])


def test_delete_requires_terminal_state_and_never_discloses_cross_tenant_pack(tmp_path) -> None:
    registry, _attestations, _runtime_gate = _registry(tmp_path)
    manifest, _files = pack()
    created = registry.quarantine(
        manifest=manifest.to_dict(), artifact_ref="artifact:one", idempotency_key="quarantine-0001"
    )
    with pytest.raises(DendriticMemoryRegistryConflict, match="delete_state_conflict"):
        registry.delete(
            tenant_id="tenant-1",
            pack_digest=manifest.digest,
            expected_revision=created["revision"],
            idempotency_key="delete-0001",
        )
    with pytest.raises(KeyError, match="not_found"):
        registry.get(tenant_id="tenant-2", pack_digest=manifest.digest)
