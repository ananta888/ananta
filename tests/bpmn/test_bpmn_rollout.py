"""Synthetic rollout boundaries: real selectors/policies, no deployment."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent.services.workflow_runtime import SignedCheckpoint
from agent.services.workflow_runtime_rollout_service import (
    InMemoryWorkflowRolloutPolicyStore,
    RolloutAwareRuntimeSelection,
    WorkflowRolloutPolicy,
    WorkflowRolloutPolicyService,
    WorkflowRolloutScope,
    WorkflowRuntimeRollbackService,
)
from agent.services.workflow_runtime_selection_composition import (
    _candidate,
    build_configured_workflow_runtime_selection,
)
from agent.services.workflow_runtime_selection_service import ExplicitFallbackPolicy, RuntimeSelectionProfile
from tests.bpmn.completion_helpers import harness as harness
from tests.bpmn.completion_helpers import linear_request
from tests.test_workflow_runtime_selection_service import _service


def scoped_plan():
    plan = linear_request().plan
    return replace(
        plan,
        metadata={
            **plan.metadata,
            "workflow_rollout_scope": {"project_id": "synthetic-project", "tenant_id": plan.tenant_id},
        },
    )


def rollout_policy(mode):
    return WorkflowRolloutPolicy(
        scope=WorkflowRolloutScope("synthetic-project"),
        policy_version=f"synthetic-{mode}",
        mode=mode,
        preferred_runtime="ananta-native",
        allowed_runtimes=("ananta-native", "temporal", "langgraph"),
        required_capabilities=("bpmn_control_v1", "audit", "authorization", "policy", "side_effect_guard"),
    )


@pytest.mark.parametrize("target", ["temporal", "langgraph"])
def test_explicit_fallback_never_silently_downgrades_bpmn(monkeypatch, target):
    monkeypatch.delenv("ANANTA_BPMN_EXECUTION_ENABLED", raising=False)
    # The existing fixture grants synthetic release eligibility only, allowing
    # this test to isolate real capability/fallback selection independently.
    selection, audit = _service(
        (
            _candidate("ananta-native", native_production=True),
            _candidate(target, native_production=True),
        )
    )
    result = selection.select(
        plan=scoped_plan(),
        profile=RuntimeSelectionProfile(
            profile_id="synthetic-fallback",
            preferred_runtime="ananta-native",
            allowed_runtimes=("ananta-native", target),
            required_capabilities=(),
            explicit_fallback_policy=ExplicitFallbackPolicy(
                enabled=True,
                allowed_runtimes=(target,),
                semantic_class="equivalent",
            ),
        ),
    )
    assert result.mode == "incompatible"
    assert not result.runtime_id
    assert audit.records
    assert all("capabilit" in item["reason_code"] for item in result.rejected)


@pytest.mark.parametrize("mode", ["drain", "disabled", "shadow"])
def test_admission_stop_keeps_existing_bpmn_instance_bound_and_drainable(harness, monkeypatch, mode):
    request = replace(linear_request(), plan=scoped_plan())
    running = harness.dispatch_first(request)
    plan_hash = request.plan.plan_hash
    policies = WorkflowRolloutPolicyService(InMemoryWorkflowRolloutPolicyStore(), clock=lambda: 100.0)
    policies.set_policy(
        rollout_policy(mode),
        expected_revision=0,
        actor_id="synthetic-policy",
        reason_code="synthetic-admission-stop",
        change_id=f"synthetic-stop-{mode}",
    )
    monkeypatch.delenv("ANANTA_BPMN_EXECUTION_ENABLED", raising=False)
    selection, audit = _service((_candidate("ananta-native", native_production=True),))
    result = RolloutAwareRuntimeSelection(policies=policies, selection=selection).select(
        plan=request.plan,
        preferred_runtime="ananta-native",
        allowed_runtimes=("ananta-native",),
    )
    assert result.mode == "blocked"
    assert mode in result.reason_code
    assert audit.records == []  # Policy stops admission before any runtime selection.
    assert harness.hub.checkpoint(request) == running.checkpoint
    harness.restart()
    finished = harness.finish(request)
    assert finished.status == "completed"
    assert finished.checkpoint.plan_hash == plan_hash
    assert harness.handler.calls == ["work", "after"]


@pytest.mark.parametrize("target", ["temporal", "langgraph"])
def test_repeated_rollback_with_capability_loss_never_mutates_policy(monkeypatch, target):
    monkeypatch.setenv("ANANTA_BPMN_EXECUTION_ENABLED", "true")
    policies = WorkflowRolloutPolicyService(InMemoryWorkflowRolloutPolicyStore(), clock=lambda: 100.0)
    baseline = policies.set_policy(
        rollout_policy("drain"),
        expected_revision=0,
        actor_id="synthetic-policy",
        reason_code="synthetic-drain",
        change_id="synthetic-drain-policy",
    )
    selection, _ = _service((_candidate(target, native_production=True),))
    rollback = WorkflowRuntimeRollbackService(policies=policies, selection=selection)
    before = policies.store.list_audit(baseline.policy.scope)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="rollback_target_not_safe|rollback_capability_loss"):
            rollback.rollback(
                scope=baseline.policy.scope,
                plan=scoped_plan(),
                target_runtime=target,
                policy_version="synthetic-downgrade",
                expected_revision=baseline.revision,
                actor_id="synthetic-operator",
                reason_code="synthetic-rollback",
                change_id="synthetic-rollback-attempt",
            )
        assert policies.store.get(baseline.policy.scope) == baseline
        assert policies.store.list_audit(baseline.policy.scope) == before


@pytest.mark.parametrize(
    "runtime_id,runtime_version",
    [
        ("ananta-native", "0.9.0"),
        ("ananta-native", "999.0.0"),
        ("temporal", "1.0.0"),
    ],
)
def test_signed_checkpoint_from_different_runtime_contract_cannot_resume(harness, runtime_id, runtime_version):
    request = linear_request()
    checkpoint = harness.dispatch_first(request).checkpoint
    foreign = SignedCheckpoint.issue(
        key_ring=harness.keys,
        tenant_id=checkpoint.tenant_id,
        workflow_id=checkpoint.workflow_id,
        run_id=checkpoint.run_id,
        task_id=checkpoint.task_id,
        plan_hash=checkpoint.plan_hash,
        policy_version=checkpoint.policy_version,
        runtime_id=runtime_id,
        runtime_version=runtime_version,
        state=checkpoint.state,
        revision=checkpoint.revision + 1,
        fencing_token=checkpoint.fencing_token,
        now=100.0,
    )
    harness.stores["checkpoints"].save(foreign, expected_revision=checkpoint.revision)
    before = harness.hub.stream(request)
    harness.restart()
    with pytest.raises(ValueError, match="runtime_version_unsupported|cross_runtime_checkpoint_denied"):
        harness.hub.advance(request)
    assert harness.handler.calls == ["work"]
    assert harness.hub.stream(request) == before


@pytest.mark.parametrize("enabled", [False, True])
def test_opt_in_flag_alone_is_not_production_release_evidence(monkeypatch, enabled):
    monkeypatch.setenv("ANANTA_BPMN_EXECUTION_ENABLED", "true" if enabled else "false")
    from agent.services.workflow_runtime_selection_service import InMemoryRuntimeSelectionAudit

    selector = build_configured_workflow_runtime_selection(
        SimpleNamespace(backend_id="ananta-native"),
        native_production=True,
        audit=InMemoryRuntimeSelectionAudit(),
    )
    result = selector.select(plan=scoped_plan(), preferred_runtime="ananta-native", allowed_runtimes=("ananta-native",))
    assert result.mode == "blocked"
    assert not result.runtime_id
