from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent.services.workflow_runtime.events import CanonicalWorkflowEvent, InMemoryEventStore
from agent.services.workflow_runtime.execution_plan import ExecutionNode, ExecutionPlan
from agent.services.workflow_runtime.security import HmacKeyRing
from agent.services.workflow_shadow_comparison_service import (
    HubEventWorkflowShadowComparisonProducer,
    JsonWorkflowShadowComparisonEvidenceStore,
    WorkflowShadowComparisonService,
    WorkflowShadowObservation,
    WorkflowShadowRuntimeIdentity,
)


def _keys() -> HmacKeyRing:
    return HmacKeyRing({"shadow-v1": b"shadow-evidence-test-key-material"}, active_key_id="shadow-v1")


def _observation(runtime_id: str, run_id: str) -> WorkflowShadowObservation:
    return WorkflowShadowObservation.build(
        runtime_id=runtime_id,
        runtime_version="1.0.0",
        runtime_build="build-a",
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id=run_id,
        plan_hash="plan-hash-v1",
        terminal_status="completed",
        capabilities=("audit", "checkpoint", "resume", "side_effect_guard"),
        event_types=("workflow.run.started", "workflow.node.completed", "workflow.run.completed"),
        artifact_contracts={"report": "ananta.example_report.v1"},
        invariants={"event_sequence_contiguous": True, "terminal_success": True},
    )


def _comparison(*, clock: float = 1_000.0):
    return WorkflowShadowComparisonService(key_ring=_keys(), clock=lambda: clock).compare(
        baseline=_observation("ananta-native", "baseline-run"),
        shadow=_observation("langgraph", "shadow-run"),
        required_capabilities={"checkpoint", "resume"},
        source_revision="revision-a",
        scope_key="scope-a",
        policy_hash="policy-hash-a",
        policy_version="shadow-v1",
        policy_revision=2,
    )


def test_shadow_comparison_is_signed_content_addressed_and_not_a_runtime_success() -> None:
    first = _comparison()
    second = _comparison()

    assert first == second
    assert first.status == "passed"
    assert first.promotion_safe is True
    assert first.evidence_ref.startswith("wsc-")
    assert first.signature
    assert first.to_dict()["production_eligible"] is False
    first.verify(
        key_ring=_keys(),
        now=1_001.0,
        scope_key="scope-a",
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        runtime_id="langgraph",
        runtime_version="1.0.0",
        runtime_build="build-a",
        plan_hash="plan-hash-v1",
        policy_hash="policy-hash-a",
        policy_version="shadow-v1",
        policy_revision=2,
        source_revision="revision-a",
    )


def test_shadow_comparison_blocks_capability_and_semantic_drift() -> None:
    service = WorkflowShadowComparisonService(key_ring=_keys(), clock=lambda: 1_000.0)
    incompatible = service.compare(
        baseline=_observation("ananta-native", "baseline-run"),
        shadow=replace(_observation("langgraph", "shadow-run"), capabilities=("audit",)),
        required_capabilities={"checkpoint", "resume"},
        source_revision="revision-a",
        scope_key="scope-a",
        policy_hash="policy-hash-a",
        policy_version="shadow-v1",
        policy_revision=2,
    )
    drifted = service.compare(
        baseline=_observation("ananta-native", "baseline-run"),
        shadow=replace(
            _observation("langgraph", "shadow-run"),
            event_types=("workflow.run.started", "workflow.shadow.drift", "workflow.run.completed"),
        ),
        required_capabilities={"audit"},
        source_revision="revision-a",
        scope_key="scope-a",
        policy_hash="policy-hash-a",
        policy_version="shadow-v1",
        policy_revision=2,
    )

    assert incompatible.status == "incompatible"
    with pytest.raises(RuntimeError, match="comparison_incompatible"):
        incompatible.assert_promotion_safe()
    assert drifted.status == "failed"
    assert "event_invariant_drift" in drifted.deviations


@pytest.mark.parametrize(
    "changes, match",
    [
        ({"terminal_status": "failed"}, "not_successful"),
        ({"event_types": ()}, "evidence_empty"),
        ({"invariants": ()}, "evidence_empty"),
    ],
)
def test_shadow_observation_rejects_failed_or_empty_claims(changes: dict, match: str) -> None:
    with pytest.raises((ValueError, RuntimeError), match=match):
        replace(_observation("langgraph", "shadow-run"), **changes).assert_valid()


def test_shadow_observation_does_not_coerce_truthy_strings() -> None:
    with pytest.raises(ValueError, match="boolean_required"):
        WorkflowShadowObservation.build(
            runtime_id="langgraph",
            runtime_version="1",
            runtime_build="build",
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            run_id="run-a",
            plan_hash="plan-a",
            terminal_status="completed",
            capabilities=("audit",),
            event_types=("workflow.run.completed",),
            artifact_contracts={},
            invariants={"terminal_success": "false"},  # type: ignore[dict-item]
        )


def test_json_store_requires_owner_only_signed_fresh_fully_bound_evidence(tmp_path: Path) -> None:
    comparison = _comparison()
    path = tmp_path / "shadow.json"
    path.write_text(json.dumps(comparison.to_dict()), encoding="utf-8")
    store = JsonWorkflowShadowComparisonEvidenceStore(
        path,
        key_ring=_keys(),
        expected_source_revision="revision-a",
        clock=lambda: 1_001.0,
    )
    bindings = {
        "scope_key": "scope-a",
        "tenant_id": "tenant-a",
        "workflow_id": "workflow-a",
        "runtime_id": "langgraph",
        "runtime_version": "1.0.0",
        "runtime_build": "build-a",
        "plan_hash": "plan-hash-v1",
        "policy_hash": "policy-hash-a",
        "policy_version": "shadow-v1",
        "policy_revision": 2,
    }

    with pytest.raises(PermissionError, match="not_owner_only"):
        store.get_evidence(**bindings)
    path.chmod(0o600)
    assert store.get_evidence(**bindings) == comparison
    with pytest.raises(ValueError, match="tenant_id_mismatch"):
        store.get_evidence(**{**bindings, "tenant_id": "tenant-b"})

    tampered = comparison.to_dict()
    tampered["policy_revision"] = 3
    path.write_text(json.dumps(tampered), encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(ValueError, match="evidence_tampered"):
        store.get_evidence(**bindings)

    stale_store = JsonWorkflowShadowComparisonEvidenceStore(
        path,
        key_ring=_keys(),
        expected_source_revision="revision-a",
        clock=lambda: 5_000.0,
    )
    path.write_text(json.dumps(comparison.to_dict()), encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(ValueError, match="evidence_stale"):
        stale_store.get_evidence(**bindings)


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        tenant_id="tenant-a",
        plan_id="plan-a",
        workflow_id="workflow-a",
        policy_version="policy-a",
        nodes=(ExecutionNode(node_id="node-a"),),
        capabilities=("audit", "side_effect_guard"),
    )


def _append_run(store: InMemoryEventStore, plan: ExecutionPlan, run_id: str, *, terminal: str = "completed") -> None:
    events = (
        ("workflow.run.started", "", {"plan_hash": plan.plan_hash}),
        ("workflow.node.completed", "node-a", {"node_id": "node-a"}),
        (f"workflow.run.{terminal}", "", {}),
    )
    for index, (event_type, step_id, payload) in enumerate(events):
        store.append(
            CanonicalWorkflowEvent.build(
                tenant_id=plan.tenant_id,
                workflow_id=plan.workflow_id,
                run_id=run_id,
                event_type=event_type,
                correlation_id=run_id,
                causation_id=f"cause-{index}",
                dedupe_key=f"{run_id}-{index}",
                step_id=step_id,
                payload=payload,
                occurred_at=900.0 + index,
            ),
            expected_sequence=index,
        )


def test_hub_event_producer_derives_observations_and_rejects_failed_or_empty_runs() -> None:
    plan = _plan()
    events = InMemoryEventStore()
    _append_run(events, plan, "baseline-run")
    _append_run(events, plan, "shadow-run")
    identity = WorkflowShadowRuntimeIdentity(
        runtime_id="ananta-native",
        runtime_version="1.0.0",
        runtime_build="build-a",
        capabilities=plan.capabilities,
    )
    producer = HubEventWorkflowShadowComparisonProducer(
        events=events,
        comparison=WorkflowShadowComparisonService(key_ring=_keys(), clock=lambda: 1_000.0),
    )

    comparison = producer.produce(
        plan=plan,
        scope_key="scope-a",
        policy_hash="policy-a",
        policy_version="shadow-v1",
        policy_revision=2,
        baseline=replace(identity, runtime_id="langgraph"),
        baseline_run_id="baseline-run",
        shadow=identity,
        shadow_run_id="shadow-run",
        source_revision="revision-a",
    )
    assert comparison.status == "passed"
    assert comparison.baseline_run_id == "baseline-run"

    with pytest.raises(ValueError, match="event_sequence_empty"):
        producer.produce(
            plan=plan,
            scope_key="scope-a",
            policy_hash="policy-a",
            policy_version="shadow-v1",
            policy_revision=2,
            baseline=replace(identity, runtime_id="langgraph"),
            baseline_run_id="missing-run",
            shadow=identity,
            shadow_run_id="shadow-run",
            source_revision="revision-a",
        )

    failed_events = InMemoryEventStore()
    _append_run(failed_events, plan, "baseline-run")
    _append_run(failed_events, plan, "shadow-run", terminal="failed")
    failed_producer = HubEventWorkflowShadowComparisonProducer(
        events=failed_events,
        comparison=WorkflowShadowComparisonService(key_ring=_keys(), clock=lambda: 1_000.0),
    )
    with pytest.raises(RuntimeError, match="terminal_success"):
        failed_producer.produce(
            plan=plan,
            scope_key="scope-a",
            policy_hash="policy-a",
            policy_version="shadow-v1",
            policy_revision=2,
            baseline=replace(identity, runtime_id="langgraph"),
            baseline_run_id="baseline-run",
            shadow=identity,
            shadow_run_id="shadow-run",
            source_revision="revision-a",
        )
