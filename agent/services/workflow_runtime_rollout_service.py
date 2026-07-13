"""Hub-owned, scope-safe rollout policy for workflow runtimes.

The hierarchy is deliberately explicit: project -> tenant -> profile ->
workflow.  A child policy is a complete policy and may only narrow the policy
of its immediate parent.  Runtime execution remains outside this module; the
Hub consumes the effective policy before delegating a task to a worker.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Protocol

from agent.services.workflow_control_service import RuntimeSelection, RuntimeSelectionPort
from agent.services.workflow_runtime._serialization import redact_json, sha256_json
from agent.services.workflow_runtime.execution_plan import SIDE_EFFECT_CLASSES, ExecutionPlan
from agent.services.workflow_runtime.security import SignatureVerificationKeyRingPort
from agent.services.workflow_runtime_rollout_constraints import (
    WorkflowRolloutScope,
    assert_rollout_policy_allows_plan,
    canonical_runtime_id,
    rollout_scope_from_plan,
)
from agent.services.workflow_runtime_rollout_constraints import (
    assert_safe_shadow_to_live_transition as _assert_safe_shadow_to_live_transition,
)
from agent.services.workflow_runtime_rollout_constraints import (
    runtime_tuple as _runtime_tuple,
)
from agent.services.workflow_runtime_rollout_constraints import (
    string_tuple as _string_tuple,
)
from agent.services.workflow_runtime_selection_service import (
    ExplicitFallbackPolicy,
    RuntimeSelectionProfile,
)
from agent.services.workflow_shadow_comparison_service import WorkflowShadowComparison

WORKFLOW_ROLLOUT_POLICY_SCHEMA = "ananta.workflow_runtime_rollout_policy.v1"
WORKFLOW_ROLLOUT_AUDIT_SCHEMA = "ananta.workflow_runtime_rollout_audit.v1"
WORKFLOW_SHADOW_DECISION_SCHEMA = "ananta.workflow_shadow_effect_decision.v1"
ROLLOUT_MODES = frozenset({"disabled", "shadow", "live", "drain"})
ROLLOUT_SCOPE_TYPES = ("project", "tenant", "profile", "workflow")
PROTECTED_ROLLBACK_CAPABILITIES = frozenset({"audit", "authorization", "policy", "side_effect_guard"})

_MODE_NARROWING = {
    "disabled": frozenset({"disabled"}),
    "shadow": frozenset({"disabled", "shadow"}),
    "drain": frozenset({"disabled", "drain"}),
    "live": frozenset({"disabled", "shadow", "drain", "live"}),
}
_PERFORMANCE_PROMOTION_ADMISSION = object()
_CAPABILITY_ROLLBACK_ADMISSION = object()


@dataclass(frozen=True)
class WorkflowRolloutPolicy:
    scope: WorkflowRolloutScope
    policy_version: str
    mode: str
    preferred_runtime: str
    allowed_runtimes: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    allowed_side_effect_classes: tuple[str, ...] = ("none", "read")
    allowed_egress_destinations: tuple[str, ...] = ()
    fallback_semantics: str = "none"
    evidence_refs: tuple[str, ...] = ()
    schema: str = WORKFLOW_ROLLOUT_POLICY_SCHEMA

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "WorkflowRolloutPolicy":
        scope_raw = raw.get("scope")
        if not isinstance(scope_raw, Mapping):
            raise ValueError("workflow_rollout_scope_required")
        policy = cls(
            scope=WorkflowRolloutScope.from_mapping(scope_raw),
            policy_version=str(raw.get("policy_version") or "").strip(),
            mode=str(raw.get("mode") or "").strip().lower(),
            preferred_runtime=canonical_runtime_id(str(raw.get("preferred_runtime") or "")),
            allowed_runtimes=_runtime_tuple(raw.get("allowed_runtimes") or ()),
            required_capabilities=_string_tuple(raw.get("required_capabilities") or ()),
            allowed_side_effect_classes=_string_tuple(raw.get("allowed_side_effect_classes") or ()),
            allowed_egress_destinations=_string_tuple(raw.get("allowed_egress_destinations") or ()),
            fallback_semantics=str(raw.get("fallback_semantics") or "none").strip().lower(),
            evidence_refs=_string_tuple(raw.get("evidence_refs") or ()),
            schema=str(raw.get("schema") or WORKFLOW_ROLLOUT_POLICY_SCHEMA),
        )
        policy.assert_valid()
        return policy

    def assert_valid(self) -> None:
        self.scope.assert_valid()
        if self.schema != WORKFLOW_ROLLOUT_POLICY_SCHEMA:
            raise ValueError("workflow_rollout_policy_schema_unsupported")
        if not self.policy_version:
            raise ValueError("workflow_rollout_policy_version_required")
        if self.mode not in ROLLOUT_MODES:
            raise ValueError("workflow_rollout_mode_invalid")
        if self.fallback_semantics not in {"none", "equivalent-only"}:
            raise ValueError("workflow_rollout_fallback_semantics_invalid")
        if set(self.allowed_side_effect_classes) - set(SIDE_EFFECT_CLASSES):
            raise ValueError("workflow_rollout_side_effect_class_invalid")
        if self.mode in {"shadow", "live", "drain"} and not self.allowed_runtimes:
            raise ValueError("workflow_rollout_allowed_runtime_required")
        if self.preferred_runtime and self.preferred_runtime not in self.allowed_runtimes:
            raise ValueError("workflow_rollout_preferred_runtime_not_allowed")
        if self.mode in {"shadow", "live"} and not self.preferred_runtime:
            raise ValueError("workflow_rollout_preferred_runtime_required")
        if self.fallback_semantics == "equivalent-only" and len(self.allowed_runtimes) < 2:
            raise ValueError("workflow_rollout_fallback_target_required")
        if self.mode == "shadow" and set(self.allowed_side_effect_classes) - {"none", "read"}:
            raise ValueError("workflow_rollout_shadow_write_class_denied")

    def assert_narrows(self, parent: "WorkflowRolloutPolicy") -> None:
        if self.scope.parent() != parent.scope:
            raise ValueError("workflow_rollout_parent_scope_mismatch")
        if self.mode not in _MODE_NARROWING[parent.mode]:
            raise ValueError("workflow_rollout_mode_widening_denied")
        if set(self.allowed_runtimes) - set(parent.allowed_runtimes):
            raise ValueError("workflow_rollout_runtime_widening_denied")
        if set(parent.required_capabilities) - set(self.required_capabilities):
            raise ValueError("workflow_rollout_capability_narrowing_lost")
        if set(self.allowed_side_effect_classes) - set(parent.allowed_side_effect_classes):
            raise ValueError("workflow_rollout_side_effect_widening_denied")
        if set(self.allowed_egress_destinations) - set(parent.allowed_egress_destinations):
            raise ValueError("workflow_rollout_egress_widening_denied")
        if parent.fallback_semantics == "none" and self.fallback_semantics != "none":
            raise ValueError("workflow_rollout_fallback_widening_denied")

    def to_selection_profile(self) -> RuntimeSelectionProfile:
        fallback_targets = tuple(runtime for runtime in self.allowed_runtimes if runtime != self.preferred_runtime)
        fallback_enabled = self.fallback_semantics == "equivalent-only"
        return RuntimeSelectionProfile(
            profile_id=f"rollout:{self.scope.scope_key}:{self.policy_version}",
            preferred_runtime=self.preferred_runtime,
            allowed_runtimes=self.allowed_runtimes,
            required_capabilities=self.required_capabilities,
            explicit_fallback_policy=ExplicitFallbackPolicy(
                enabled=fallback_enabled,
                allowed_runtimes=fallback_targets if fallback_enabled else (),
                semantic_class="equivalent",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "scope": self.scope.to_dict(),
            "scope_type": self.scope.scope_type,
            "policy_version": self.policy_version,
            "mode": self.mode,
            "preferred_runtime": self.preferred_runtime,
            "allowed_runtimes": list(self.allowed_runtimes),
            "required_capabilities": list(self.required_capabilities),
            "allowed_side_effect_classes": list(self.allowed_side_effect_classes),
            "allowed_egress_destinations": list(self.allowed_egress_destinations),
            "fallback_semantics": self.fallback_semantics,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class WorkflowRolloutAuditEvent:
    event_id: str
    scope: WorkflowRolloutScope
    action: str
    actor_id: str
    reason_code: str
    occurred_at: float
    details: dict[str, Any] = field(default_factory=dict)
    schema: str = WORKFLOW_ROLLOUT_AUDIT_SCHEMA

    def assert_valid(self) -> None:
        self.scope.assert_valid()
        if self.schema != WORKFLOW_ROLLOUT_AUDIT_SCHEMA:
            raise ValueError("workflow_rollout_audit_schema_unsupported")
        if not all((self.event_id, self.action, self.actor_id, self.reason_code)):
            raise ValueError("workflow_rollout_audit_binding_required")
        if self.occurred_at <= 0:
            raise ValueError("workflow_rollout_audit_timestamp_invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "event_id": self.event_id,
            "scope": self.scope.to_dict(),
            "scope_type": self.scope.scope_type,
            "action": self.action,
            "actor_id": self.actor_id,
            "reason_code": self.reason_code,
            "occurred_at": self.occurred_at,
            "details": dict(redact_json(self.details)),
        }


@dataclass(frozen=True)
class StoredWorkflowRolloutPolicy:
    policy: WorkflowRolloutPolicy
    revision: int
    updated_at: float


class WorkflowRolloutPolicyStore(Protocol):
    def get(self, scope: WorkflowRolloutScope) -> StoredWorkflowRolloutPolicy | None: ...

    def commit(
        self,
        policy: WorkflowRolloutPolicy,
        *,
        expected_revision: int,
        parent_revision: int | None,
        audit: WorkflowRolloutAuditEvent,
    ) -> StoredWorkflowRolloutPolicy: ...

    def append_audit(self, event: WorkflowRolloutAuditEvent) -> None: ...

    def list_audit(self, scope: WorkflowRolloutScope) -> tuple[WorkflowRolloutAuditEvent, ...]: ...


class InMemoryWorkflowRolloutPolicyStore:
    """Reference store with the same parent/CAS semantics as the SQL adapter."""

    def __init__(self) -> None:
        self._policies: dict[str, StoredWorkflowRolloutPolicy] = {}
        self._audit: dict[str, WorkflowRolloutAuditEvent] = {}
        self._lock = threading.RLock()

    def get(self, scope: WorkflowRolloutScope) -> StoredWorkflowRolloutPolicy | None:
        with self._lock:
            return self._policies.get(scope.scope_key)

    def commit(
        self,
        policy: WorkflowRolloutPolicy,
        *,
        expected_revision: int,
        parent_revision: int | None,
        audit: WorkflowRolloutAuditEvent,
    ) -> StoredWorkflowRolloutPolicy:
        with self._lock:
            current = self._policies.get(policy.scope.scope_key)
            actual = current.revision if current is not None else 0
            if actual != int(expected_revision):
                raise RuntimeError("workflow_rollout_policy_cas_conflict")
            parent_scope = policy.scope.parent()
            if parent_scope is not None:
                parent = self._policies.get(parent_scope.scope_key)
                if parent is None or parent.revision != int(parent_revision or 0):
                    raise RuntimeError("workflow_rollout_parent_revision_conflict")
            if audit.event_id in self._audit:
                raise RuntimeError("workflow_rollout_audit_event_duplicate")
            stored = StoredWorkflowRolloutPolicy(
                policy=policy,
                revision=actual + 1,
                updated_at=audit.occurred_at,
            )
            self._policies[policy.scope.scope_key] = stored
            self._audit[audit.event_id] = audit
            return stored

    def append_audit(self, event: WorkflowRolloutAuditEvent) -> None:
        event.assert_valid()
        with self._lock:
            if event.event_id in self._audit:
                raise RuntimeError("workflow_rollout_audit_event_duplicate")
            self._audit[event.event_id] = event

    def list_audit(self, scope: WorkflowRolloutScope) -> tuple[WorkflowRolloutAuditEvent, ...]:
        with self._lock:
            values = [event for event in self._audit.values() if event.scope == scope]
        return tuple(sorted(values, key=lambda item: (item.occurred_at, item.event_id)))


@dataclass(frozen=True)
class EffectiveWorkflowRolloutPolicy:
    policy: WorkflowRolloutPolicy
    revisions: tuple[tuple[str, int], ...]


class WorkflowRolloutPolicyService:
    """Validate hierarchy and persist one audited, CAS-protected policy change."""

    def __init__(self, store: WorkflowRolloutPolicyStore, *, clock=time.time) -> None:
        self._store = store
        self._clock = clock

    def set_policy(
        self,
        policy: WorkflowRolloutPolicy,
        *,
        expected_revision: int,
        actor_id: str,
        reason_code: str,
        change_id: str,
        action: str = "policy_updated",
    ) -> StoredWorkflowRolloutPolicy:
        if policy.mode == "live":
            raise ValueError("workflow_rollout_live_requires_admission_service")
        return self._commit_policy(
            policy,
            expected_revision=expected_revision,
            actor_id=actor_id,
            reason_code=reason_code,
            change_id=change_id,
            action=action,
            live_admission=None,
        )

    def _commit_policy(
        self,
        policy: WorkflowRolloutPolicy,
        *,
        expected_revision: int,
        actor_id: str,
        reason_code: str,
        change_id: str,
        action: str,
        live_admission: object | None,
        approval_id: str = "",
    ) -> StoredWorkflowRolloutPolicy:
        policy.assert_valid()
        admitted_action = None
        if live_admission is _PERFORMANCE_PROMOTION_ADMISSION:
            admitted_action = "performance_safe_promotion"
        elif live_admission is _CAPABILITY_ROLLBACK_ADMISSION:
            admitted_action = "capability_safe_rollback"
        if policy.mode == "live" and (admitted_action is None or action != admitted_action):
            raise ValueError("workflow_rollout_live_admission_invalid")
        if not all((str(actor_id).strip(), str(reason_code).strip(), str(change_id).strip())):
            raise ValueError("workflow_rollout_change_audit_required")
        parent_scope = policy.scope.parent()
        parent = self._store.get(parent_scope) if parent_scope is not None else None
        if parent_scope is not None:
            if parent is None:
                raise ValueError("workflow_rollout_parent_policy_required")
            policy.assert_narrows(parent.policy)
        occurred_at = float(self._clock())
        event = WorkflowRolloutAuditEvent(
            event_id=str(change_id),
            scope=policy.scope,
            action=str(action),
            actor_id=str(actor_id),
            reason_code=str(reason_code),
            occurred_at=occurred_at,
            details={
                "policy_hash": sha256_json(policy.to_dict()),
                "policy_version": policy.policy_version,
                "mode": policy.mode,
                "parent_scope_key": parent_scope.scope_key if parent_scope else "",
                "parent_revision": parent.revision if parent else 0,
                "evidence_refs": list(policy.evidence_refs),
                "approval_id": str(approval_id),
            },
        )
        event.assert_valid()
        return self._store.commit(
            policy,
            expected_revision=int(expected_revision),
            parent_revision=parent.revision if parent else None,
            audit=event,
        )

    def resolve(self, scope: WorkflowRolloutScope) -> EffectiveWorkflowRolloutPolicy:
        scope.assert_valid()
        records: list[StoredWorkflowRolloutPolicy] = []
        for item in scope.lineage():
            record = self._store.get(item)
            if record is None:
                if item.scope_type == "project":
                    raise LookupError("workflow_rollout_project_policy_not_found")
                continue
            if records:
                record.policy.assert_narrows(records[-1].policy)
            records.append(record)
        if not records:
            raise LookupError("workflow_rollout_policy_not_found")
        return EffectiveWorkflowRolloutPolicy(
            policy=records[-1].policy,
            revisions=tuple((record.policy.scope.scope_type, record.revision) for record in records),
        )

    @property
    def store(self) -> WorkflowRolloutPolicyStore:
        return self._store


class RolloutAwareRuntimeSelection:
    """Apply the effective Hub rollout policy before runtime selection."""

    def __init__(
        self,
        *,
        policies: WorkflowRolloutPolicyService,
        selection: RuntimeSelectionPort,
    ) -> None:
        self._policies = policies
        self._selection = selection

    def select(
        self,
        *,
        plan: ExecutionPlan,
        preferred_runtime: str,
        allowed_runtimes: tuple[str, ...],
        profile: Any | None = None,
        context: Any | None = None,
    ) -> RuntimeSelection:
        del preferred_runtime, allowed_runtimes, profile
        scope = rollout_scope_from_plan(plan)
        effective = self._policies.resolve(scope)
        policy = effective.policy
        assert_rollout_policy_allows_plan(policy=policy, plan=plan, plan_scope=scope)
        if policy.mode != "live":
            return RuntimeSelection(
                runtime_id="",
                capabilities=frozenset(),
                mode="blocked",
                reason_code=f"workflow_rollout_{policy.mode}_active_run_denied",
                profile_id=f"rollout:{policy.scope.scope_key}:{policy.policy_version}",
            )
        return self._selection.select(
            plan=plan,
            preferred_runtime="",
            allowed_runtimes=(),
            profile=policy.to_selection_profile(),
            context=context,
        )


@dataclass(frozen=True)
class WorkflowShadowIntent:
    intent_id: str
    scope: WorkflowRolloutScope
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    intent_type: str
    side_effect_class: str
    target: str = ""
    payload_digest: str = ""

    def assert_valid(self) -> None:
        self.scope.assert_valid()
        if not all((self.intent_id, self.tenant_id, self.workflow_id, self.run_id, self.step_id)):
            raise ValueError("workflow_shadow_intent_binding_required")
        if self.intent_type not in {"egress", "write"}:
            raise ValueError("workflow_shadow_intent_type_invalid")
        if self.side_effect_class not in SIDE_EFFECT_CLASSES:
            raise ValueError("workflow_shadow_side_effect_class_invalid")
        if self.scope.tenant_id and self.scope.tenant_id != self.tenant_id:
            raise ValueError("workflow_shadow_intent_tenant_mismatch")
        if self.scope.workflow_id and self.scope.workflow_id != self.workflow_id:
            raise ValueError("workflow_shadow_intent_workflow_mismatch")
        if self.intent_type == "egress" and not self.target:
            raise ValueError("workflow_shadow_egress_target_required")
        if len(self.payload_digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.payload_digest.lower()
        ):
            raise ValueError("workflow_shadow_intent_payload_digest_required")


@dataclass(frozen=True)
class WorkflowShadowEffectDecision:
    intent_id: str
    allowed: bool
    suppressed: bool
    reason_code: str
    audit_ref: str
    schema: str = WORKFLOW_SHADOW_DECISION_SCHEMA


class WorkflowShadowPort(Protocol):
    def suppress_and_record_intent(self, intent: WorkflowShadowIntent) -> WorkflowShadowEffectDecision: ...


class AuditedWorkflowShadowPort:
    """Fail-closed port: its interface cannot invoke egress or a write."""

    def __init__(self, store: WorkflowRolloutPolicyStore, *, clock=time.time) -> None:
        self._store = store
        self._clock = clock

    def suppress_and_record_intent(self, intent: WorkflowShadowIntent) -> WorkflowShadowEffectDecision:
        intent.assert_valid()
        event_id = f"shadow-{sha256_json(_shadow_audit_identity(intent))}"
        event = WorkflowRolloutAuditEvent(
            event_id=event_id,
            scope=intent.scope,
            action="shadow_intent_suppressed",
            actor_id="hub-shadow-policy",
            reason_code="workflow_shadow_egress_or_write_suppressed",
            occurred_at=float(self._clock()),
            details={
                **_shadow_audit_identity(intent),
                "target_digest": sha256_json({"target": intent.target}),
            },
        )
        self._store.append_audit(event)
        return WorkflowShadowEffectDecision(
            intent_id=intent.intent_id,
            allowed=False,
            suppressed=True,
            reason_code=event.reason_code,
            audit_ref=event.event_id,
        )


class WorkflowRolloutEffectConsumer:
    """Authorize safe reads and route shadow egress/write intents to suppression."""

    def __init__(
        self,
        *,
        policies: WorkflowRolloutPolicyService,
        shadow: WorkflowShadowPort,
    ) -> None:
        self._policies = policies
        self._shadow = shadow

    def evaluate(self, intent: WorkflowShadowIntent) -> WorkflowShadowEffectDecision:
        intent.assert_valid()
        policy = self._policies.resolve(intent.scope).policy
        is_write = intent.side_effect_class in {"idempotent_write", "non_idempotent_write"}
        is_egress = intent.intent_type == "egress" or bool(intent.target)
        if policy.mode == "shadow" and (is_write or is_egress):
            return self._shadow.suppress_and_record_intent(intent)
        if intent.side_effect_class not in policy.allowed_side_effect_classes:
            return WorkflowShadowEffectDecision(
                intent_id=intent.intent_id,
                allowed=False,
                suppressed=False,
                reason_code="workflow_rollout_side_effect_not_allowed",
                audit_ref="",
            )
        if intent.target and intent.target not in policy.allowed_egress_destinations:
            return WorkflowShadowEffectDecision(
                intent_id=intent.intent_id,
                allowed=False,
                suppressed=False,
                reason_code="workflow_rollout_egress_not_allowed",
                audit_ref="",
            )
        return WorkflowShadowEffectDecision(
            intent_id=intent.intent_id,
            allowed=policy.mode == "live",
            suppressed=False,
            reason_code=(
                "workflow_rollout_effect_allowed"
                if policy.mode == "live"
                else f"workflow_rollout_{policy.mode}_effect_denied"
            ),
            audit_ref="",
        )


@dataclass(frozen=True)
class WorkflowRollbackResult:
    stored_policy: StoredWorkflowRolloutPolicy
    runtime_selection: RuntimeSelection


@dataclass(frozen=True)
class WorkflowRolloutPerformanceEvidence:
    evidence_ref: str
    runtime_id: str
    start_p95_ms: float
    signal_p95_ms: float
    event_projection_p95_ms: float
    worker_restart_resume_p95_ms: float
    source_revision: str

    def assert_promotion_safe(self) -> None:
        if not self.evidence_ref or not self.source_revision:
            raise ValueError("workflow_rollout_performance_evidence_binding_required")
        if canonical_runtime_id(self.runtime_id) != self.runtime_id:
            raise ValueError("workflow_rollout_performance_runtime_invalid")
        values = (
            self.start_p95_ms,
            self.signal_p95_ms,
            self.event_projection_p95_ms,
            self.worker_restart_resume_p95_ms,
        )
        if any(value < 0 for value in values):
            raise ValueError("workflow_rollout_performance_metric_invalid")
        if self.start_p95_ms >= 2_000:
            raise RuntimeError("workflow_rollout_start_p95_exceeded")
        if self.signal_p95_ms >= 2_000:
            raise RuntimeError("workflow_rollout_signal_p95_exceeded")
        if self.event_projection_p95_ms >= 1_000:
            raise RuntimeError("workflow_rollout_event_projection_p95_exceeded")
        if self.worker_restart_resume_p95_ms >= 30_000:
            raise RuntimeError("workflow_rollout_worker_restart_resume_p95_exceeded")


class WorkflowRolloutPerformanceEvidencePort(Protocol):
    def get_evidence(
        self,
        *,
        scope: WorkflowRolloutScope,
        runtime_id: str,
    ) -> WorkflowRolloutPerformanceEvidence: ...


class WorkflowShadowComparisonEvidencePort(Protocol):
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
    ) -> WorkflowShadowComparison: ...


class WorkflowPromotionApprovalPort(Protocol):
    """Verify a grant for exactly one immutable promotion request."""

    def verify(
        self,
        *,
        approval_id: str,
        policy: WorkflowRolloutPolicy,
        plan: ExecutionPlan,
        expected_revision: int,
        change_id: str,
    ) -> str: ...


class ApprovalRequestWorkflowPromotionApproval:
    """Small adapter over the Hub approval service; safe for service reuse."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def verify(
        self,
        *,
        approval_id: str,
        policy: WorkflowRolloutPolicy,
        plan: ExecutionPlan,
        expected_revision: int,
        change_id: str,
    ) -> str:
        normalized = str(approval_id).strip()
        if not normalized:
            raise ValueError("workflow_rollout_promotion_approval_required")
        arguments = {
            "tenant_id": plan.tenant_id,
            "workflow_id": plan.workflow_id,
            "scope": policy.scope.to_dict(),
            "policy_hash": sha256_json(policy.to_dict()),
            "plan_hash": plan.plan_hash,
            "expected_revision": int(expected_revision),
            "change_id": str(change_id),
        }
        grant = self._service.resolve_grant_for_call(
            tool_name="workflow.runtime.promote",
            arguments=arguments,
            target_fingerprint=policy.scope.scope_key,
        )
        if grant is None or str(grant.id) != normalized:
            raise PermissionError("workflow_rollout_promotion_approval_invalid")
        return normalized


@dataclass(frozen=True)
class WorkflowPromotionResult:
    stored_policy: StoredWorkflowRolloutPolicy
    runtime_selection: RuntimeSelection
    performance_evidence: WorkflowRolloutPerformanceEvidence
    shadow_comparison_evidence: WorkflowShadowComparison


class WorkflowRuntimePromotionService:
    """Promote only after common selection and explicit P95 evidence pass."""

    def __init__(
        self,
        *,
        policies: WorkflowRolloutPolicyService,
        selection: RuntimeSelectionPort,
        performance: WorkflowRolloutPerformanceEvidencePort,
        shadow_comparison: WorkflowShadowComparisonEvidencePort,
        approval: WorkflowPromotionApprovalPort | None = None,
        evidence_keys: SignatureVerificationKeyRingPort | None = None,
        expected_source_revision: str = "",
        clock=time.time,
    ) -> None:
        self._policies = policies
        self._selection = selection
        self._performance = performance
        self._shadow_comparison = shadow_comparison
        self._approval = approval
        self._evidence_keys = evidence_keys
        self._expected_source_revision = str(expected_source_revision).strip()
        self._clock = clock

    def promote(
        self,
        *,
        policy: WorkflowRolloutPolicy,
        plan: ExecutionPlan,
        expected_revision: int,
        actor_id: str,
        reason_code: str,
        change_id: str,
        approval_id: str,
    ) -> WorkflowPromotionResult:
        if policy.mode != "live":
            raise ValueError("workflow_rollout_promotion_live_policy_required")
        plan_scope = rollout_scope_from_plan(plan)
        assert_rollout_policy_allows_plan(policy=policy, plan=plan, plan_scope=plan_scope)
        current = self._policies.store.get(policy.scope)
        if current is None or current.revision != int(expected_revision) or current.policy.mode != "shadow":
            raise RuntimeError("workflow_rollout_shadow_baseline_required")
        _assert_safe_shadow_to_live_transition(current.policy, policy)
        if self._approval is None:
            raise RuntimeError("workflow_rollout_promotion_approval_verifier_required")
        verified_approval_id = self._approval.verify(
            approval_id=approval_id,
            policy=policy,
            plan=plan,
            expected_revision=expected_revision,
            change_id=change_id,
        )
        if verified_approval_id != str(approval_id).strip():
            raise PermissionError("workflow_rollout_promotion_approval_invalid")
        profile = policy.to_selection_profile()
        selection = self._selection.select(
            plan=plan,
            preferred_runtime="",
            allowed_runtimes=(),
            profile=profile,
            context=None,
        )
        if selection.runtime_id != policy.preferred_runtime or selection.mode not in {"live", "durable"}:
            raise RuntimeError(
                "workflow_rollout_promotion_target_not_safe:" + (selection.reason_code or "runtime_selection_failed")
            )
        if not selection.runtime_version or not selection.runtime_build:
            raise RuntimeError("workflow_rollout_runtime_build_identity_unavailable")
        if not self._expected_source_revision:
            raise RuntimeError("workflow_rollout_source_revision_verifier_required")
        evidence = self._performance.get_evidence(
            scope=policy.scope,
            runtime_id=policy.preferred_runtime,
        )
        if evidence.runtime_id != policy.preferred_runtime:
            raise RuntimeError("workflow_rollout_performance_runtime_mismatch")
        if evidence.source_revision != self._expected_source_revision:
            raise RuntimeError("workflow_rollout_performance_revision_mismatch")
        evidence.assert_promotion_safe()
        shadow_policy_hash = sha256_json(current.policy.to_dict())
        shadow_evidence = self._shadow_comparison.get_evidence(
            scope_key=policy.scope.scope_key,
            tenant_id=plan.tenant_id,
            workflow_id=plan.workflow_id,
            runtime_id=policy.preferred_runtime,
            runtime_version=selection.runtime_version,
            runtime_build=selection.runtime_build,
            plan_hash=plan.plan_hash,
            policy_hash=shadow_policy_hash,
            policy_version=current.policy.policy_version,
            policy_revision=current.revision,
        )
        if self._evidence_keys is None:
            raise RuntimeError("workflow_rollout_shadow_evidence_verifier_required")
        shadow_evidence.verify(
            key_ring=self._evidence_keys,
            now=self._clock(),
            scope_key=policy.scope.scope_key,
            tenant_id=plan.tenant_id,
            workflow_id=plan.workflow_id,
            runtime_id=policy.preferred_runtime,
            runtime_version=selection.runtime_version,
            runtime_build=selection.runtime_build,
            plan_hash=plan.plan_hash,
            policy_hash=shadow_policy_hash,
            policy_version=current.policy.policy_version,
            policy_revision=current.revision,
            source_revision=self._expected_source_revision,
        )
        safe_policy = replace(
            policy,
            evidence_refs=tuple(
                sorted(
                    {
                        *policy.evidence_refs,
                        selection.audit_ref,
                        evidence.evidence_ref,
                        shadow_evidence.evidence_ref,
                        f"approval:{verified_approval_id}",
                    }
                    - {""}
                )
            ),
        )
        stored = self._policies._commit_policy(
            safe_policy,
            expected_revision=expected_revision,
            actor_id=actor_id,
            reason_code=reason_code,
            change_id=change_id,
            action="performance_safe_promotion",
            live_admission=_PERFORMANCE_PROMOTION_ADMISSION,
            approval_id=verified_approval_id,
        )
        return WorkflowPromotionResult(stored, selection, evidence, shadow_evidence)


class WorkflowRuntimeRollbackService:
    """Route only new runs to a target proven safe by the common selector."""

    def __init__(
        self,
        *,
        policies: WorkflowRolloutPolicyService,
        selection: RuntimeSelectionPort,
    ) -> None:
        self._policies = policies
        self._selection = selection

    def rollback(
        self,
        *,
        scope: WorkflowRolloutScope,
        plan: ExecutionPlan,
        target_runtime: str,
        policy_version: str,
        expected_revision: int,
        actor_id: str,
        reason_code: str,
        change_id: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> WorkflowRollbackResult:
        plan_scope = rollout_scope_from_plan(plan)
        if scope not in plan_scope.lineage():
            raise ValueError("workflow_rollout_plan_scope_mismatch")
        effective = self._policies.resolve(scope)
        current = effective.policy
        assert_rollout_policy_allows_plan(policy=current, plan=plan, plan_scope=plan_scope)
        target = canonical_runtime_id(target_runtime)
        required = tuple(
            sorted(set(current.required_capabilities) | set(plan.capabilities) | set(PROTECTED_ROLLBACK_CAPABILITIES))
        )
        profile = RuntimeSelectionProfile(
            profile_id=f"rollback:{scope.scope_key}:{policy_version}",
            preferred_runtime=target,
            allowed_runtimes=(target,),
            required_capabilities=required,
            explicit_fallback_policy=ExplicitFallbackPolicy(),
        )
        selection = self._selection.select(
            plan=plan,
            preferred_runtime="",
            allowed_runtimes=(),
            profile=profile,
            context=None,
        )
        if selection.runtime_id != target or selection.mode not in {"live", "durable"}:
            raise RuntimeError(
                "workflow_rollout_rollback_target_not_safe:" + (selection.reason_code or "runtime_selection_failed")
            )
        if set(required) - set(selection.capabilities):
            raise RuntimeError("workflow_rollout_rollback_capability_loss")
        next_policy = replace(
            current,
            scope=scope,
            policy_version=str(policy_version),
            mode="live",
            preferred_runtime=target,
            allowed_runtimes=(target,),
            required_capabilities=required,
            fallback_semantics="none",
            evidence_refs=tuple(sorted({*evidence_refs, selection.audit_ref} - {""})),
        )
        stored = self._policies._commit_policy(
            next_policy,
            expected_revision=expected_revision,
            actor_id=actor_id,
            reason_code=reason_code,
            change_id=change_id,
            action="capability_safe_rollback",
            live_admission=_CAPABILITY_ROLLBACK_ADMISSION,
        )
        return WorkflowRollbackResult(stored_policy=stored, runtime_selection=selection)


def _shadow_audit_identity(intent: WorkflowShadowIntent) -> dict[str, Any]:
    return {
        "intent_id": intent.intent_id,
        "tenant_id": intent.tenant_id,
        "workflow_id": intent.workflow_id,
        "run_id": intent.run_id,
        "step_id": intent.step_id,
        "intent_type": intent.intent_type,
        "side_effect_class": intent.side_effect_class,
        "payload_digest": intent.payload_digest,
    }


__all__ = [
    "ApprovalRequestWorkflowPromotionApproval",
    "AuditedWorkflowShadowPort",
    "EffectiveWorkflowRolloutPolicy",
    "InMemoryWorkflowRolloutPolicyStore",
    "PROTECTED_ROLLBACK_CAPABILITIES",
    "RolloutAwareRuntimeSelection",
    "StoredWorkflowRolloutPolicy",
    "WorkflowRollbackResult",
    "WorkflowPromotionResult",
    "WorkflowPromotionApprovalPort",
    "WorkflowRolloutAuditEvent",
    "WorkflowRolloutEffectConsumer",
    "WorkflowRolloutPolicy",
    "WorkflowRolloutPolicyService",
    "WorkflowRolloutPolicyStore",
    "WorkflowRolloutScope",
    "WorkflowRuntimeRollbackService",
    "WorkflowRolloutPerformanceEvidence",
    "WorkflowRolloutPerformanceEvidencePort",
    "WorkflowRuntimePromotionService",
    "WorkflowShadowEffectDecision",
    "WorkflowShadowIntent",
    "WorkflowShadowPort",
    "assert_rollout_policy_allows_plan",
    "canonical_runtime_id",
    "rollout_scope_from_plan",
]
