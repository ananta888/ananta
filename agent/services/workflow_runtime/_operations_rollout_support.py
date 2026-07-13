"""Deterministic fixtures used by the AIR-055 rollout operations drill."""

from __future__ import annotations

import base64

from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent, InMemoryEventStore
from agent.services.workflow_runtime.execution_plan import (
    ExecutionBudget,
    ExecutionNode,
    ExecutionPlan,
)
from agent.services.workflow_runtime.security import (
    SignatureSigningKeyRingPort,
    SignatureVerificationKeyRingPort,
)
from agent.services.workflow_runtime_rollout_service import (
    WorkflowRolloutPerformanceEvidence,
    WorkflowRolloutPolicy,
    WorkflowRolloutScope,
)
from agent.services.workflow_runtime_selection_service import (
    InMemoryRuntimeCatalog,
    InMemoryRuntimeHealthService,
    InMemoryRuntimeSelectionAudit,
    RuntimeCandidate,
    StrictRuntimeBudgetService,
    StrictRuntimeDataLocalityService,
    VersionBoundRuntimePolicy,
    WorkflowRuntimeSelectionService,
)
from agent.services.workflow_shadow_comparison_service import (
    HubEventWorkflowShadowComparisonProducer,
    WorkflowShadowComparison,
    WorkflowShadowComparisonService,
    WorkflowShadowRuntimeIdentity,
)
from ananta_contracts.runtime_authorization_crypto import Ed25519SigningKeyRing


class MissingDrillPerformanceEvidence:
    def get_evidence(
        self,
        *,
        scope: WorkflowRolloutScope,
        runtime_id: str,
    ) -> WorkflowRolloutPerformanceEvidence:
        del scope, runtime_id
        raise RuntimeError("workflow_operations_promotion_evidence_unavailable")


class BoundDrillPerformanceEvidence:
    """Content-addressed staging evidence bound to suppressed shadow intents."""

    def __init__(
        self,
        *,
        source_revision: str,
        shadow_audit_refs: tuple[str, ...],
        shadow_comparison_ref: str,
    ) -> None:
        self._source_revision = str(source_revision).strip()
        self._shadow_audit_refs = tuple(sorted(set(shadow_audit_refs)))
        self._shadow_comparison_ref = str(shadow_comparison_ref).strip()

    def get_evidence(
        self,
        *,
        scope: WorkflowRolloutScope,
        runtime_id: str,
    ) -> WorkflowRolloutPerformanceEvidence:
        if (
            not self._source_revision
            or len(self._shadow_audit_refs) != 2
            or not self._shadow_comparison_ref.startswith("wsc-")
        ):
            raise RuntimeError("workflow_operations_shadow_evidence_incomplete")
        identity = {
            "scope_key": scope.scope_key,
            "runtime_id": runtime_id,
            "source_revision": self._source_revision,
            "shadow_audit_refs": list(self._shadow_audit_refs),
            "shadow_comparison_ref": self._shadow_comparison_ref,
        }
        return WorkflowRolloutPerformanceEvidence(
            evidence_ref="wrod-performance-" + sha256_json(identity),
            runtime_id=runtime_id,
            start_p95_ms=100.0,
            signal_p95_ms=100.0,
            event_projection_p95_ms=50.0,
            worker_restart_resume_p95_ms=500.0,
            source_revision=self._source_revision,
        )


class BoundDrillShadowComparisonEvidence:
    def __init__(
        self,
        comparison: WorkflowShadowComparison,
        *,
        key_ring: SignatureVerificationKeyRingPort,
    ) -> None:
        self._comparison = comparison
        self._key_ring = key_ring

    def get_evidence(
        self,
        *,
        scope_key: str,
        tenant_id: str,
        workflow_id: str,
        runtime_id: str,
        runtime_version: str,
        runtime_build: str,
        plan_hash: str,
        policy_hash: str,
        policy_version: str,
        policy_revision: int,
    ) -> WorkflowShadowComparison:
        comparison = self._comparison
        comparison.verify(
            key_ring=self._key_ring,
            now=451.0,
            scope_key=scope_key,
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            runtime_id=runtime_id,
            runtime_version=runtime_version,
            runtime_build=runtime_build,
            plan_hash=plan_hash,
            policy_hash=policy_hash,
            policy_version=policy_version,
            policy_revision=policy_revision,
        )
        return comparison


class BoundDrillPromotionApproval:
    """Digest-equivalent in-memory approval verifier for the isolated drill."""

    def __init__(self, *, policy: WorkflowRolloutPolicy, plan: ExecutionPlan, revision: int) -> None:
        self._policy_hash = sha256_json(policy.to_dict())
        self._plan_hash = plan.plan_hash
        self._scope_key = policy.scope.scope_key
        self._revision = int(revision)

    def verify(
        self,
        *,
        approval_id: str,
        policy: WorkflowRolloutPolicy,
        plan: ExecutionPlan,
        expected_revision: int,
        change_id: str,
    ) -> str:
        expected_id = f"approval-{change_id}"
        if not approval_id:
            raise ValueError("workflow_rollout_promotion_approval_required")
        if (
            approval_id != expected_id
            or sha256_json(policy.to_dict()) != self._policy_hash
            or plan.plan_hash != self._plan_hash
            or policy.scope.scope_key != self._scope_key
            or int(expected_revision) != self._revision
        ):
            raise PermissionError("workflow_rollout_promotion_approval_invalid")
        return approval_id


def drill_evidence_key_ring() -> SignatureSigningKeyRingPort:
    return Ed25519SigningKeyRing(
        {"drill-evidence-v1": base64.b64encode(b"workflow-drill-evidence-key-v1!!")},
        active_key_id="drill-evidence-v1",
    )


def drill_shadow_comparison(
    *,
    source_revision: str,
    semantic_drift: bool = False,
) -> WorkflowShadowComparison:
    plan = rollout_drill_plan()
    events = InMemoryEventStore()
    scope = WorkflowRolloutScope(project_id="project-rollout-drill")
    shadow_policy = rollout_lifecycle_policy(scope, version="shadow-v1", mode="shadow")
    policy_hash = sha256_json(shadow_policy.to_dict())
    _append_shadow_run_events(
        events,
        plan=plan,
        run_id="baseline-run",
        runtime_id="langgraph",
        policy_hash=policy_hash,
    )
    _append_shadow_run_events(
        events,
        plan=plan,
        run_id="shadow-run",
        runtime_id="ananta-native",
        policy_hash=policy_hash,
        semantic_drift=semantic_drift,
    )
    key_ring = drill_evidence_key_ring()
    comparison = HubEventWorkflowShadowComparisonProducer(
        events=events,
        comparison=WorkflowShadowComparisonService(
            key_ring=key_ring,
            clock=lambda: 450.0,
        ),
    ).produce(
        plan=plan,
        scope_key=scope.scope_key,
        policy_hash=policy_hash,
        policy_version=shadow_policy.policy_version,
        policy_revision=2,
        baseline=WorkflowShadowRuntimeIdentity(
            runtime_id="langgraph",
            runtime_version="operations-drill-v1",
            runtime_build="operations-drill-build-v1",
            capabilities=plan.capabilities,
        ),
        baseline_run_id="baseline-run",
        shadow=WorkflowShadowRuntimeIdentity(
            runtime_id="ananta-native",
            runtime_version="operations-drill-v1",
            runtime_build="operations-drill-build-v1",
            capabilities=plan.capabilities,
        ),
        shadow_run_id="shadow-run",
        source_revision=source_revision,
    )
    if not semantic_drift:
        comparison.assert_promotion_safe()
    return comparison


def _append_shadow_run_events(
    store: InMemoryEventStore,
    *,
    plan: ExecutionPlan,
    run_id: str,
    runtime_id: str,
    policy_hash: str,
    semantic_drift: bool = False,
) -> None:
    types = [
        (
            "workflow.run.started",
            "",
            {
                "plan_hash": plan.plan_hash,
                "runtime_id": runtime_id,
                "runtime_version": "operations-drill-v1",
                "runtime_build": "operations-drill-build-v1",
                "rollout_policy_hash": policy_hash,
                "rollout_policy_version": "shadow-v1",
                "rollout_policy_revision": 2,
            },
        ),
        ("workflow.node.completed", plan.nodes[0].node_id, {"node_id": plan.nodes[0].node_id}),
    ]
    if semantic_drift:
        types.append(("workflow.shadow.semantic.drift", "", {}))
    types.append(("workflow.run.completed", "", {}))
    for index, (event_type, step_id, payload) in enumerate(types):
        store.append(
            CanonicalWorkflowEvent.build(
                tenant_id=plan.tenant_id,
                workflow_id=plan.workflow_id,
                run_id=run_id,
                event_type=event_type,
                correlation_id=f"correlation-{run_id}",
                causation_id=f"cause-{index}",
                dedupe_key=f"{run_id}-{index}",
                step_id=step_id,
                payload=payload,
                occurred_at=400.0 + index,
            ),
            expected_sequence=index,
        )


class _DrillReleaseEvidence:
    """Fail closed unless the deterministic candidate declaration matches."""

    def __init__(self, candidates: tuple[RuntimeCandidate, ...]) -> None:
        self._candidates = {candidate.runtime_id: candidate for candidate in candidates}

    def evaluate(
        self,
        *,
        plan: ExecutionPlan,
        runtime_id: str,
        runtime_version: str,
        required_capabilities: frozenset[str],
    ) -> tuple[bool, str]:
        del plan
        candidate = self._candidates.get(runtime_id)
        if candidate is None:
            return False, "runtime_release_candidate_unknown"
        if not runtime_version or runtime_version != candidate.version:
            return False, "runtime_release_version_mismatch"
        if required_capabilities - candidate.capabilities:
            return False, "runtime_release_capability_evidence_missing"
        return True, "runtime_release_evidence_verified"


def rollout_lifecycle_policy(
    scope: WorkflowRolloutScope,
    *,
    version: str,
    mode: str,
) -> WorkflowRolloutPolicy:
    active = mode in {"shadow", "live", "drain"}
    return WorkflowRolloutPolicy(
        scope=scope,
        policy_version=version,
        mode=mode,
        preferred_runtime="ananta-native" if mode in {"shadow", "live"} else "",
        allowed_runtimes=("ananta-native", "langgraph") if active else (),
        required_capabilities=(
            "approval",
            "audit",
            "authorization",
            "checkpoint",
            "policy",
            "resume",
            "side_effect_guard",
        ),
        allowed_side_effect_classes=("none", "read"),
        fallback_semantics="none",
        evidence_refs=("operations-drill",),
    )


def rollout_drill_plan() -> ExecutionPlan:
    return ExecutionPlan(
        tenant_id="tenant-drill",
        plan_id="plan-rollout-drill",
        workflow_id="workflow-rollout-drill",
        policy_version="policy-drill",
        nodes=(
            ExecutionNode(
                node_id="step-rollout-drill",
                required_capabilities=("checkpoint", "resume"),
            ),
        ),
        capabilities=(
            "approval",
            "audit",
            "authorization",
            "checkpoint",
            "policy",
            "resume",
            "side_effect_guard",
        ),
        budget=ExecutionBudget(
            max_attempts=1,
            timeout_seconds=60.0,
            max_tokens=100,
            max_cost_micros=100,
        ),
        metadata={
            "data_locality": "eu",
            "workflow_rollout_scope": {
                "project_id": "project-rollout-drill",
                "tenant_id": "tenant-drill",
            },
        },
    )


def drill_runtime_candidates() -> tuple[RuntimeCandidate, ...]:
    capabilities = frozenset(
        {
            "approval",
            "audit",
            "authorization",
            "checkpoint",
            "policy",
            "resume",
            "side_effect_guard",
        }
    )
    return tuple(
        RuntimeCandidate(
            runtime_id=runtime_id,
            capabilities=capabilities,
            mode="live",
            data_localities=frozenset({"eu"}),
            policy_versions=frozenset({"policy-drill"}),
            max_timeout_seconds=60.0,
            max_tokens=100,
            max_cost_micros=100,
            priority=priority,
            version="operations-drill-v1",
            build_id="operations-drill-build-v1",
        )
        for runtime_id, priority in (("ananta-native", 10), ("langgraph", 20))
    )


def drill_runtime_selector(
    candidates: tuple[RuntimeCandidate, ...],
    audit: InMemoryRuntimeSelectionAudit,
) -> WorkflowRuntimeSelectionService:
    health = {candidate.runtime_id: "ready" for candidate in candidates}
    return WorkflowRuntimeSelectionService(
        catalog=InMemoryRuntimeCatalog(candidates),
        health=InMemoryRuntimeHealthService(health),
        policy=VersionBoundRuntimePolicy(),
        locality=StrictRuntimeDataLocalityService(),
        budget=StrictRuntimeBudgetService(),
        audit=audit,
        release_evidence=_DrillReleaseEvidence(candidates),
    )


__all__ = [
    "BoundDrillPerformanceEvidence",
    "BoundDrillPromotionApproval",
    "BoundDrillShadowComparisonEvidence",
    "MissingDrillPerformanceEvidence",
    "drill_evidence_key_ring",
    "drill_runtime_candidates",
    "drill_runtime_selector",
    "drill_shadow_comparison",
    "rollout_drill_plan",
    "rollout_lifecycle_policy",
]
