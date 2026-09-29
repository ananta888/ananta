from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent.services.workflow_control_service import RuntimeSelection
from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime.execution_plan import ExecutionNode, ExecutionPlan
from agent.services.workflow_runtime.security import HmacKeyRing
from agent.services.workflow_runtime_rollout_service import (
    ApprovalRequestWorkflowPromotionApproval,
    InMemoryWorkflowRolloutPolicyStore,
    RolloutAwareRuntimeSelection,
    WorkflowRolloutPerformanceEvidence,
    WorkflowRolloutPolicy,
    WorkflowRolloutPolicyService,
    WorkflowRolloutScope,
    WorkflowRuntimePromotionService,
    WorkflowShadowIntent,
)
from agent.services.workflow_shadow_comparison_service import (
    WorkflowShadowComparisonService,
    WorkflowShadowObservation,
)


def _scope() -> WorkflowRolloutScope:
    return WorkflowRolloutScope("project-a")


def _plan(
    *,
    effect: str = "read",
    egress: tuple[str, ...] = (),
    scope: dict | None = None,
) -> ExecutionPlan:
    metadata: dict = {"data_locality": "eu"}
    if scope is not None:
        metadata["workflow_rollout_scope"] = scope
    return ExecutionPlan(
        tenant_id="tenant-a",
        plan_id="plan-a",
        workflow_id="workflow-a",
        policy_version="policy-a",
        nodes=(
            ExecutionNode(
                node_id="node-a",
                side_effect_class=effect,
                metadata={"egress_destinations": list(egress)} if egress else {},
            ),
        ),
        capabilities=("audit", "side_effect_guard"),
        metadata=metadata,
    )


def _bound_plan(**kwargs) -> ExecutionPlan:
    return _plan(
        scope={"project_id": "project-a", "tenant_id": "tenant-a"},
        **kwargs,
    )


def _policy(*, mode: str = "shadow", egress: tuple[str, ...] = ()) -> WorkflowRolloutPolicy:
    return WorkflowRolloutPolicy(
        scope=_scope(),
        policy_version=f"{mode}-v1",
        mode=mode,
        preferred_runtime="ananta-native",
        allowed_runtimes=("ananta-native", "langgraph"),
        required_capabilities=("audit", "side_effect_guard"),
        allowed_side_effect_classes=("none", "read"),
        allowed_egress_destinations=egress,
    )


class _Selection:
    def __init__(self) -> None:
        self.calls = 0

    def select(self, **_kwargs) -> RuntimeSelection:
        self.calls += 1
        return RuntimeSelection(
            runtime_id="ananta-native",
            capabilities=frozenset({"audit", "side_effect_guard"}),
            mode="live",
            reason_code="runtime_selected_preferred",
            audit_ref="selection-a",
            runtime_version="1.0.0",
            runtime_build="build-a",
        )


def _shadow_policies() -> WorkflowRolloutPolicyService:
    policies = WorkflowRolloutPolicyService(InMemoryWorkflowRolloutPolicyStore(), clock=lambda: 900.0)
    policies.set_policy(
        _policy(),
        expected_revision=0,
        actor_id="operator-a",
        reason_code="shadow-baseline",
        change_id="shadow-change",
    )
    return policies


def test_rollout_selection_has_no_missing_or_foreign_scope_bypass() -> None:
    selection = _Selection()
    rollout = RolloutAwareRuntimeSelection(policies=_shadow_policies(), selection=selection)

    with pytest.raises(ValueError, match="plan_scope_required"):
        rollout.select(plan=_plan(), preferred_runtime="ananta-native", allowed_runtimes=("ananta-native",))
    with pytest.raises(ValueError, match="plan_tenant_mismatch"):
        rollout.select(
            plan=_plan(scope={"project_id": "project-a", "tenant_id": "tenant-foreign"}),
            preferred_runtime="ananta-native",
            allowed_runtimes=("ananta-native",),
        )
    with pytest.raises(ValueError, match="plan_workflow_mismatch"):
        rollout.select(
            plan=_plan(
                scope={
                    "project_id": "project-a",
                    "tenant_id": "tenant-a",
                    "profile_id": "profile-a",
                    "workflow_id": "workflow-foreign",
                }
            ),
            preferred_runtime="ananta-native",
            allowed_runtimes=("ananta-native",),
        )
    assert selection.calls == 0


def test_rollout_selection_enforces_plan_side_effects_and_egress_before_delegation() -> None:
    selection = _Selection()
    rollout = RolloutAwareRuntimeSelection(policies=_shadow_policies(), selection=selection)

    with pytest.raises(PermissionError, match="plan_side_effect_denied"):
        rollout.select(
            plan=_bound_plan(effect="idempotent_write"),
            preferred_runtime="ananta-native",
            allowed_runtimes=("ananta-native",),
        )
    with pytest.raises(PermissionError, match="plan_egress_denied"):
        rollout.select(
            plan=_bound_plan(egress=("https://blocked.invalid",)),
            preferred_runtime="ananta-native",
            allowed_runtimes=("ananta-native",),
        )
    blocked = rollout.select(
        plan=_bound_plan(),
        preferred_runtime="ananta-native",
        allowed_runtimes=("ananta-native",),
    )
    assert blocked.mode == "blocked"
    assert selection.calls == 0


def test_shadow_intent_rejects_cross_scope_and_unbound_payload() -> None:
    common = {
        "intent_id": "intent-a",
        "scope": WorkflowRolloutScope("project-a", "tenant-a"),
        "tenant_id": "tenant-foreign",
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "step_id": "step-a",
        "intent_type": "write",
        "side_effect_class": "idempotent_write",
        "payload_digest": "d" * 64,
    }
    with pytest.raises(ValueError, match="tenant_mismatch"):
        WorkflowShadowIntent(**common).assert_valid()
    with pytest.raises(ValueError, match="payload_digest_required"):
        WorkflowShadowIntent(**{**common, "tenant_id": "tenant-a", "payload_digest": ""}).assert_valid()


def _keys() -> HmacKeyRing:
    return HmacKeyRing({"shadow-v1": b"workflow-rollout-shadow-test-key"}, active_key_id="shadow-v1")


def _observation(runtime: str, run_id: str) -> WorkflowShadowObservation:
    return WorkflowShadowObservation.build(
        runtime_id=runtime,
        runtime_version="1.0.0",
        runtime_build="build-a",
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id=run_id,
        plan_hash=_bound_plan().plan_hash,
        terminal_status="completed",
        capabilities=("audit", "side_effect_guard"),
        event_types=("workflow.run.started", "workflow.node.completed", "workflow.run.completed"),
        artifact_contracts={},
        invariants={"plan_nodes_observed": True, "terminal_success": True},
    )


class _Performance:
    def get_evidence(self, *, scope, runtime_id):
        del scope
        return WorkflowRolloutPerformanceEvidence(
            evidence_ref="performance-a",
            runtime_id=runtime_id,
            start_p95_ms=10.0,
            signal_p95_ms=10.0,
            event_projection_p95_ms=10.0,
            worker_restart_resume_p95_ms=10.0,
            source_revision="revision-a",
        )


class _Evidence:
    def __init__(self, value) -> None:
        self.value = value
        self.bindings: dict = {}

    def get_evidence(self, **bindings):
        self.bindings = bindings
        return self.value


class _Approval:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[dict] = []

    def verify(self, **bindings):
        self.calls.append(bindings)
        if not self.allowed:
            raise PermissionError("workflow_rollout_promotion_approval_invalid")
        return str(bindings["approval_id"])


def _promotion_fixture():
    policies = _shadow_policies()
    plan = _bound_plan()
    current = policies.store.get(_scope())
    assert current is not None
    live = replace(current.policy, policy_version="live-v1", mode="live")
    comparison = WorkflowShadowComparisonService(key_ring=_keys(), clock=lambda: 1_000.0).compare(
        baseline=_observation("langgraph", "baseline-run"),
        shadow=_observation("ananta-native", "shadow-run"),
        required_capabilities=set(plan.capabilities),
        source_revision="revision-a",
        scope_key=_scope().scope_key,
        policy_hash=sha256_json(current.policy.to_dict()),
        policy_version=current.policy.policy_version,
        policy_revision=current.revision,
    )
    return policies, plan, live, comparison


def _promotion_service(*, approval=None, evidence=None):
    policies, plan, live, comparison = _promotion_fixture()
    evidence_port = _Evidence(evidence or comparison)
    service = WorkflowRuntimePromotionService(
        policies=policies,
        selection=_Selection(),
        performance=_Performance(),
        shadow_comparison=evidence_port,
        approval=approval,
        evidence_keys=_keys(),
        expected_source_revision="revision-a",
        clock=lambda: 1_001.0,
    )
    return service, policies, plan, live, evidence_port


def test_promotion_enforces_approval_inside_service_and_binds_every_evidence_dimension() -> None:
    service, _policies, plan, live, evidence_port = _promotion_service(approval=None)
    with pytest.raises(RuntimeError, match="approval_verifier_required"):
        service.promote(
            policy=live,
            plan=plan,
            expected_revision=1,
            actor_id="operator-a",
            reason_code="test",
            change_id="change-a",
            approval_id="approval-a",
        )

    approval = _Approval()
    service, _policies, plan, live, evidence_port = _promotion_service(approval=approval)
    result = service.promote(
        policy=live,
        plan=plan,
        expected_revision=1,
        actor_id="operator-a",
        reason_code="test",
        change_id="change-a",
        approval_id="approval-a",
    )
    assert result.stored_policy.policy.mode == "live"
    assert approval.calls[0]["expected_revision"] == 1
    assert evidence_port.bindings == {
        "scope_key": _scope().scope_key,
        "tenant_id": "tenant-a",
        "workflow_id": "workflow-a",
        "runtime_id": "ananta-native",
        "runtime_version": "1.0.0",
        "runtime_build": "build-a",
        "plan_hash": plan.plan_hash,
        "policy_hash": sha256_json(_policy().to_dict()),
        "policy_version": "shadow-v1",
        "policy_revision": 1,
    }


def test_promotion_rejects_policy_drift_and_signed_evidence_for_wrong_runtime_build() -> None:
    approval = _Approval()
    service, _policies, plan, live, _evidence = _promotion_service(approval=approval)
    with pytest.raises(ValueError, match="promotion_policy_drift"):
        service.promote(
            policy=replace(live, allowed_egress_destinations=("https://new.invalid",)),
            plan=plan,
            expected_revision=1,
            actor_id="operator-a",
            reason_code="test",
            change_id="change-a",
            approval_id="approval-a",
        )

    policies, plan, live, comparison = _promotion_fixture()
    wrong_build = replace(comparison, shadow_runtime_build="old-build", signature="")
    key_id, signature = _keys().sign(
        namespace=wrong_build.schema,
        payload=wrong_build._signing_payload(),
        key_id=wrong_build.key_id,
    )
    wrong_build = replace(wrong_build, key_id=key_id, signature=signature)
    service = WorkflowRuntimePromotionService(
        policies=policies,
        selection=_Selection(),
        performance=_Performance(),
        shadow_comparison=_Evidence(wrong_build),
        approval=approval,
        evidence_keys=_keys(),
        expected_source_revision="revision-a",
        clock=lambda: 1_001.0,
    )
    with pytest.raises(ValueError, match="runtime_build_mismatch"):
        service.promote(
            policy=live,
            plan=plan,
            expected_revision=1,
            actor_id="operator-a",
            reason_code="test",
            change_id="change-b",
            approval_id="approval-b",
        )


def test_approval_adapter_recomputes_exact_service_level_digest() -> None:
    plan = _bound_plan()
    policy = replace(_policy(), mode="live", policy_version="live-v1")
    grant = SimpleNamespace(id="approval-a")
    calls: list[dict] = []
    adapter = ApprovalRequestWorkflowPromotionApproval(
        SimpleNamespace(resolve_grant_for_call=lambda **kwargs: calls.append(kwargs) or grant)
    )

    assert (
        adapter.verify(
            approval_id="approval-a",
            policy=policy,
            plan=plan,
            expected_revision=2,
            change_id="change-a",
        )
        == "approval-a"
    )
    arguments = calls[0]["arguments"]
    assert arguments["tenant_id"] == "tenant-a"
    assert arguments["workflow_id"] == "workflow-a"
    assert arguments["plan_hash"] == plan.plan_hash
    assert arguments["policy_hash"] == sha256_json(policy.to_dict())
    assert calls[0]["target_fingerprint"] == policy.scope.scope_key
