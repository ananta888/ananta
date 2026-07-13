"""Capability- and profile-based workflow runtime selection owned by the Hub.

Selection is deliberately separate from execution.  Every candidate is checked
against health, capabilities, Hub policy, data locality and budget ports.  A
fallback is considered only when the profile explicitly enables an equivalent
transition accepted by the shared workflow fallback policy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from agent.services.workflow_control_service import RuntimeSelection
from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from agent.services.workflow_runtime_fallback_policy import (
    RuntimeFallbackRequest,
    WorkflowRuntimeFallbackPolicy,
    workflow_runtime_fallback_policy,
)

RUNTIME_SELECTION_PROFILE_SCHEMA = "ananta.runtime_selection_profile.v1"
RUNTIME_SELECTION_CATALOG_SCHEMA = "ananta.runtime_selection_profiles.v1"
RUNTIME_SELECTION_AUDIT_SCHEMA = "ananta.runtime_selection_audit.v1"
_RUNTIME_MODES = frozenset({"live", "durable"})
_HEALTH_STATES = frozenset({"ready", "degraded", "unavailable", "disabled"})
_FALLBACK_SEMANTICS = frozenset({"equivalent", "degraded", "incompatible"})
_BLOCKING_PREFIXES = (
    "runtime_health_",
    "runtime_policy_",
    "runtime_data_locality_",
    "runtime_budget_",
    "runtime_fallback_",
    "runtime_release_",
)


@dataclass(frozen=True)
class ExplicitFallbackPolicy:
    enabled: bool = False
    allowed_runtimes: tuple[str, ...] = ()
    semantic_class: str = "equivalent"

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "ExplicitFallbackPolicy":
        value = dict(raw or {})
        policy = cls(
            enabled=bool(value.get("enabled", False)),
            allowed_runtimes=_clean_tuple(value.get("allowed_runtimes") or ()),
            semantic_class=str(value.get("semantic_class") or "equivalent").strip(),
        )
        policy.assert_valid()
        return policy

    def assert_valid(self) -> None:
        if self.semantic_class not in _FALLBACK_SEMANTICS:
            raise ValueError("runtime_profile_fallback_semantic_class_invalid")
        if self.enabled and not self.allowed_runtimes:
            raise ValueError("runtime_profile_fallback_targets_required")
        if not self.enabled and self.allowed_runtimes:
            raise ValueError("runtime_profile_fallback_targets_without_enable")

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "allowed_runtimes": list(self.allowed_runtimes),
            "semantic_class": self.semantic_class,
        }


@dataclass(frozen=True)
class RuntimeSelectionProfile:
    profile_id: str
    preferred_runtime: str
    allowed_runtimes: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    explicit_fallback_policy: ExplicitFallbackPolicy
    schema: str = RUNTIME_SELECTION_PROFILE_SCHEMA

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, profile_id: str = "") -> "RuntimeSelectionProfile":
        value = dict(raw)
        profile = cls(
            profile_id=str(profile_id or value.get("profile_id") or value.get("id") or "").strip(),
            preferred_runtime=str(value.get("preferred_runtime") or "").strip(),
            allowed_runtimes=_clean_tuple(value.get("allowed_runtimes") or ()),
            required_capabilities=_clean_tuple(value.get("required_capabilities") or ()),
            explicit_fallback_policy=ExplicitFallbackPolicy.from_mapping(value.get("explicit_fallback_policy")),
            schema=str(value.get("schema") or RUNTIME_SELECTION_PROFILE_SCHEMA),
        )
        profile.assert_valid()
        return profile

    @classmethod
    def legacy(cls, *, preferred_runtime: str, allowed_runtimes: tuple[str, ...]) -> "RuntimeSelectionProfile":
        preferred = str(preferred_runtime).strip()
        allowed = _clean_tuple(allowed_runtimes or ((preferred,) if preferred else ()))
        return cls(
            profile_id="legacy-explicit-runtime-selection",
            preferred_runtime=preferred,
            allowed_runtimes=allowed,
            required_capabilities=(),
            explicit_fallback_policy=ExplicitFallbackPolicy(),
        )

    def assert_valid(self) -> None:
        if self.schema != RUNTIME_SELECTION_PROFILE_SCHEMA:
            raise ValueError("runtime_profile_schema_unsupported")
        if not self.profile_id or not self.preferred_runtime or not self.allowed_runtimes:
            raise ValueError("runtime_profile_identity_required")
        if self.preferred_runtime not in self.allowed_runtimes:
            raise ValueError("runtime_profile_preferred_not_allowed")
        self.explicit_fallback_policy.assert_valid()
        if set(self.explicit_fallback_policy.allowed_runtimes) - set(self.allowed_runtimes):
            raise ValueError("runtime_profile_fallback_target_not_allowed")
        if self.preferred_runtime in self.explicit_fallback_policy.allowed_runtimes:
            raise ValueError("runtime_profile_preferred_cannot_be_fallback")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "profile_id": self.profile_id,
            "preferred_runtime": self.preferred_runtime,
            "allowed_runtimes": list(self.allowed_runtimes),
            "required_capabilities": list(self.required_capabilities),
            "explicit_fallback_policy": self.explicit_fallback_policy.to_dict(),
        }


@dataclass(frozen=True)
class RuntimeCandidate:
    runtime_id: str
    capabilities: frozenset[str]
    mode: str
    data_localities: frozenset[str]
    policy_versions: frozenset[str]
    max_timeout_seconds: float | None = None
    max_tokens: int | None = None
    max_cost_micros: int | None = None
    priority: int = 100
    version: str = ""
    build_id: str = ""

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "RuntimeCandidate":
        value = dict(raw)
        candidate = cls(
            runtime_id=str(value.get("runtime_id") or "").strip(),
            capabilities=frozenset(_clean_tuple(value.get("capabilities") or ())),
            mode=str(value.get("mode") or "").strip(),
            data_localities=frozenset(_clean_tuple(value.get("data_localities") or ())),
            policy_versions=frozenset(_clean_tuple(value.get("policy_versions") or ())),
            max_timeout_seconds=_optional_float(value.get("max_timeout_seconds")),
            max_tokens=_optional_int(value.get("max_tokens")),
            max_cost_micros=_optional_int(value.get("max_cost_micros")),
            priority=int(value.get("priority", 100)),
            version=str(value.get("version") or "").strip(),
            build_id=str(value.get("build_id") or "").strip(),
        )
        candidate.assert_valid()
        return candidate

    def assert_valid(self) -> None:
        if not self.runtime_id or self.mode not in _RUNTIME_MODES:
            raise ValueError("runtime_candidate_identity_invalid")
        if not self.capabilities:
            raise ValueError("runtime_candidate_capabilities_required")
        if not self.data_localities or not self.policy_versions:
            raise ValueError("runtime_candidate_governance_metadata_required")
        if self.priority < 0:
            raise ValueError("runtime_candidate_priority_invalid")
        for value in (self.max_timeout_seconds, self.max_tokens, self.max_cost_micros):
            if value is not None and value < 0:
                raise ValueError("runtime_candidate_budget_invalid")


@dataclass(frozen=True)
class RuntimeHealthSnapshot:
    runtime_id: str
    status: str
    reason_code: str = ""

    def assert_valid(self) -> None:
        if not self.runtime_id or self.status not in _HEALTH_STATES:
            raise ValueError("runtime_health_snapshot_invalid")


@dataclass(frozen=True)
class RuntimeSelectionContext:
    required_data_locality: str = ""
    timeout_seconds: float = 0.0
    max_tokens: int | None = None
    max_cost_micros: int | None = None
    allow_degraded_health: bool = False
    policy_attributes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_plan(cls, plan: ExecutionPlan) -> "RuntimeSelectionContext":
        return cls(
            required_data_locality=str(plan.metadata.get("data_locality") or "").strip(),
            timeout_seconds=float(plan.budget.timeout_seconds),
            max_tokens=plan.budget.max_tokens,
            max_cost_micros=plan.budget.max_cost_micros,
            allow_degraded_health=bool(plan.metadata.get("allow_degraded_runtime", False)),
            policy_attributes={
                str(key): str(value)
                for key, value in dict(plan.metadata.get("runtime_policy_attributes") or {}).items()
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_data_locality": self.required_data_locality,
            "timeout_seconds": self.timeout_seconds,
            "max_tokens": self.max_tokens,
            "max_cost_micros": self.max_cost_micros,
            "allow_degraded_health": self.allow_degraded_health,
            "policy_attributes": dict(sorted(self.policy_attributes.items())),
        }


@dataclass(frozen=True)
class RuntimeEligibilityDecision:
    allowed: bool
    reason_code: str


class RuntimeCatalogPort(Protocol):
    def list_candidates(self) -> tuple[RuntimeCandidate, ...]: ...


class RuntimeHealthPort(Protocol):
    def get_health(self, runtime_id: str) -> RuntimeHealthSnapshot: ...


class RuntimePolicyEligibilityPort(Protocol):
    def evaluate(
        self,
        *,
        plan: ExecutionPlan,
        profile: RuntimeSelectionProfile,
        candidate: RuntimeCandidate,
        context: RuntimeSelectionContext,
    ) -> RuntimeEligibilityDecision: ...


class RuntimeDataLocalityPort(Protocol):
    def evaluate(
        self, *, candidate: RuntimeCandidate, context: RuntimeSelectionContext
    ) -> RuntimeEligibilityDecision: ...


class RuntimeBudgetEligibilityPort(Protocol):
    def evaluate(
        self, *, candidate: RuntimeCandidate, context: RuntimeSelectionContext
    ) -> RuntimeEligibilityDecision: ...


class RuntimeSelectionAuditPort(Protocol):
    def record(self, record: "RuntimeSelectionAuditRecord") -> None: ...


class RuntimeReleaseEvidencePort(Protocol):
    """Verify current production gate evidence before a runtime is eligible."""

    def evaluate(
        self,
        *,
        plan: ExecutionPlan,
        runtime_id: str,
        runtime_version: str,
        required_capabilities: frozenset[str],
    ) -> tuple[bool, str]: ...


@dataclass(frozen=True)
class CandidateEvaluation:
    runtime_id: str
    eligible: bool
    reason_codes: tuple[str, ...]
    health_status: str
    selected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "eligible": self.eligible,
            "selected": self.selected,
            "reason_codes": list(self.reason_codes),
            "health_status": self.health_status,
        }


@dataclass(frozen=True)
class RuntimeSelectionAuditRecord:
    audit_id: str
    tenant_id: str
    workflow_id: str
    plan_hash: str
    profile_id: str
    selected_runtime: str
    decision_reason_code: str
    mode: str
    required_capabilities: tuple[str, ...]
    evaluations: tuple[CandidateEvaluation, ...]
    schema: str = RUNTIME_SELECTION_AUDIT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "audit_id": self.audit_id,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "plan_hash": self.plan_hash,
            "profile_id": self.profile_id,
            "selected_runtime": self.selected_runtime,
            "decision_reason_code": self.decision_reason_code,
            "mode": self.mode,
            "required_capabilities": list(self.required_capabilities),
            "evaluations": [value.to_dict() for value in self.evaluations],
        }


class WorkflowRuntimeSelectionService:
    """Deterministic Hub implementation of ``RuntimeSelectionPort``."""

    def __init__(
        self,
        *,
        catalog: RuntimeCatalogPort,
        health: RuntimeHealthPort,
        policy: RuntimePolicyEligibilityPort,
        locality: RuntimeDataLocalityPort,
        budget: RuntimeBudgetEligibilityPort,
        audit: RuntimeSelectionAuditPort,
        release_evidence: RuntimeReleaseEvidencePort,
        fallback_policy: WorkflowRuntimeFallbackPolicy = workflow_runtime_fallback_policy,
    ) -> None:
        self._catalog = catalog
        self._health = health
        self._policy = policy
        self._locality = locality
        self._budget = budget
        self._audit = audit
        self._release_evidence = release_evidence
        self._fallback = fallback_policy

    def select(
        self,
        *,
        plan: ExecutionPlan,
        preferred_runtime: str = "",
        allowed_runtimes: tuple[str, ...] = (),
        profile: RuntimeSelectionProfile | Mapping[str, Any] | None = None,
        context: RuntimeSelectionContext | Mapping[str, Any] | None = None,
    ) -> RuntimeSelection:
        plan.assert_valid()
        resolved_profile = self._profile(profile, preferred_runtime, allowed_runtimes)
        resolved_context = self._context(context, plan)
        required = tuple(sorted(set(plan.capabilities) | set(resolved_profile.required_capabilities)))
        candidates = self._candidate_map()
        evaluations = {
            runtime_id: self._evaluate_candidate(
                plan=plan,
                profile=resolved_profile,
                context=resolved_context,
                candidate=candidate,
                required_capabilities=required,
            )
            for runtime_id, candidate in candidates.items()
        }
        for runtime_id in resolved_profile.allowed_runtimes:
            if runtime_id not in candidates:
                evaluations[runtime_id] = CandidateEvaluation(
                    runtime_id,
                    False,
                    ("runtime_not_registered",),
                    "unknown",
                )

        selected_runtime = ""
        decision_reason = ""
        preferred = candidates.get(resolved_profile.preferred_runtime)
        preferred_evaluation = evaluations[resolved_profile.preferred_runtime]
        if preferred is not None and preferred_evaluation.eligible:
            selected_runtime = preferred.runtime_id
            decision_reason = "runtime_selected_preferred"
        else:
            selected_runtime, fallback_reasons = self._select_fallback(
                profile=resolved_profile,
                candidates=candidates,
                evaluations=evaluations,
            )
            for runtime_id, reason_code in fallback_reasons.items():
                current = evaluations[runtime_id]
                reasons = set(current.reason_codes)
                reasons.discard("runtime_candidate_eligible")
                reasons.add(reason_code)
                evaluations[runtime_id] = CandidateEvaluation(
                    runtime_id=current.runtime_id,
                    eligible=False,
                    reason_codes=tuple(sorted(reasons)),
                    health_status=current.health_status,
                )
            if selected_runtime:
                decision_reason = "runtime_selected_explicit_fallback"

        if selected_runtime:
            selected_candidate = candidates[selected_runtime]
            evaluations = self._mark_selected_and_discard_alternatives(evaluations, selected_runtime=selected_runtime)
            mode = selected_candidate.mode
        else:
            mode = self._failure_mode(evaluations)
            decision_reason = (
                "runtime_selection_no_compatible_runtime"
                if mode == "incompatible"
                else "runtime_selection_no_safe_runtime"
            )
            selected_candidate = None

        ordered_evaluations = tuple(evaluations[key] for key in sorted(evaluations))
        audit_record = self._audit_record(
            plan=plan,
            profile=resolved_profile,
            context=resolved_context,
            selected_runtime=selected_runtime,
            decision_reason=decision_reason,
            mode=mode,
            required=required,
            evaluations=ordered_evaluations,
        )
        try:
            self._audit.record(audit_record)
        except Exception as exc:
            raise RuntimeError("runtime_selection_audit_failed") from exc
        rejected = tuple(
            {
                "runtime_id": evaluation.runtime_id,
                "reason_code": evaluation.reason_codes[0],
                "detail": ",".join(evaluation.reason_codes),
            }
            for evaluation in ordered_evaluations
            if not evaluation.selected
        )
        return RuntimeSelection(
            runtime_id=selected_runtime,
            capabilities=(selected_candidate.capabilities if selected_candidate else frozenset()),
            mode=mode,
            reason_code=decision_reason,
            rejected=rejected,
            profile_id=resolved_profile.profile_id,
            audit_ref=audit_record.audit_id,
            runtime_version=(selected_candidate.version if selected_candidate else ""),
            runtime_build=(selected_candidate.build_id if selected_candidate else ""),
        )

    def _evaluate_candidate(
        self,
        *,
        plan: ExecutionPlan,
        profile: RuntimeSelectionProfile,
        context: RuntimeSelectionContext,
        candidate: RuntimeCandidate,
        required_capabilities: tuple[str, ...],
    ) -> CandidateEvaluation:
        reasons: list[str] = []
        if candidate.runtime_id not in profile.allowed_runtimes:
            reasons.append("runtime_not_allowed_by_profile")
        missing = set(required_capabilities) - set(candidate.capabilities)
        if missing:
            reasons.append("runtime_capabilities_missing:" + ",".join(sorted(missing)))

        health = self._health.get_health(candidate.runtime_id)
        health.assert_valid()
        if health.runtime_id != candidate.runtime_id:
            reasons.append("runtime_health_binding_mismatch")
        elif health.status == "degraded" and not context.allow_degraded_health:
            reasons.append("runtime_health_degraded")
        elif health.status not in {"ready", "degraded"}:
            reasons.append("runtime_health_unavailable")

        for decision in (
            self._policy.evaluate(
                plan=plan,
                profile=profile,
                candidate=candidate,
                context=context,
            ),
            self._locality.evaluate(candidate=candidate, context=context),
            self._budget.evaluate(candidate=candidate, context=context),
        ):
            if not decision.allowed:
                reasons.append(decision.reason_code)
        try:
            release_allowed, release_reason = self._release_evidence.evaluate(
                plan=plan,
                runtime_id=candidate.runtime_id,
                runtime_version=candidate.version,
                required_capabilities=frozenset(required_capabilities),
            )
            if not isinstance(release_allowed, bool) or not str(release_reason).startswith("runtime_release_"):
                reasons.append("runtime_release_evidence_response_invalid")
            elif not release_allowed:
                reasons.append(str(release_reason))
        except Exception:
            reasons.append("runtime_release_evidence_unavailable")
        return CandidateEvaluation(
            runtime_id=candidate.runtime_id,
            eligible=not reasons,
            reason_codes=tuple(dict.fromkeys(reasons)) or ("runtime_candidate_eligible",),
            health_status=health.status,
        )

    def _select_fallback(
        self,
        *,
        profile: RuntimeSelectionProfile,
        candidates: dict[str, RuntimeCandidate],
        evaluations: dict[str, CandidateEvaluation],
    ) -> tuple[str, dict[str, str]]:
        reasons: dict[str, str] = {}
        source = candidates.get(profile.preferred_runtime)
        for runtime_id in self._ordered_fallbacks(profile, candidates):
            evaluation = evaluations[runtime_id]
            if not evaluation.eligible:
                continue
            if source is None:
                reasons[runtime_id] = "runtime_fallback_source_unknown"
                continue
            enabled = (
                profile.explicit_fallback_policy.enabled
                and runtime_id in profile.explicit_fallback_policy.allowed_runtimes
            )
            decision = self._fallback.evaluate(
                RuntimeFallbackRequest.create(
                    source_runtime=source.runtime_id,
                    target_runtime=runtime_id,
                    reason_code="preferred_runtime_ineligible",
                    semantic_class=profile.explicit_fallback_policy.semantic_class,
                    source_capabilities=source.capabilities,
                    target_capabilities=candidates[runtime_id].capabilities,
                    explicitly_enabled=enabled,
                )
            )
            if decision.allowed:
                return runtime_id, reasons
            reasons[runtime_id] = decision.reason_code
        return "", reasons

    @staticmethod
    def _ordered_fallbacks(
        profile: RuntimeSelectionProfile, candidates: dict[str, RuntimeCandidate]
    ) -> tuple[str, ...]:
        values = [
            candidate
            for runtime_id, candidate in candidates.items()
            if runtime_id in profile.allowed_runtimes and runtime_id != profile.preferred_runtime
        ]
        return tuple(
            candidate.runtime_id
            for candidate in sorted(
                values,
                key=lambda value: (value.priority, value.runtime_id),
            )
        )

    @staticmethod
    def _mark_selected_and_discard_alternatives(
        evaluations: dict[str, CandidateEvaluation], *, selected_runtime: str
    ) -> dict[str, CandidateEvaluation]:
        result: dict[str, CandidateEvaluation] = {}
        for runtime_id, evaluation in evaluations.items():
            if runtime_id == selected_runtime:
                result[runtime_id] = CandidateEvaluation(
                    runtime_id,
                    True,
                    ("runtime_candidate_selected",),
                    evaluation.health_status,
                    selected=True,
                )
            elif evaluation.eligible:
                result[runtime_id] = CandidateEvaluation(
                    runtime_id,
                    False,
                    ("runtime_not_selected_lower_rank",),
                    evaluation.health_status,
                )
            else:
                result[runtime_id] = evaluation
        return result

    @staticmethod
    def _failure_mode(evaluations: dict[str, CandidateEvaluation]) -> str:
        reasons = {reason for value in evaluations.values() for reason in value.reason_codes}
        return "blocked" if any(reason.startswith(_BLOCKING_PREFIXES) for reason in reasons) else "incompatible"

    def _candidate_map(self) -> dict[str, RuntimeCandidate]:
        result: dict[str, RuntimeCandidate] = {}
        for candidate in self._catalog.list_candidates():
            candidate.assert_valid()
            if candidate.runtime_id in result:
                raise ValueError("runtime_catalog_duplicate_runtime_id")
            result[candidate.runtime_id] = candidate
        return result

    @staticmethod
    def _profile(
        profile: RuntimeSelectionProfile | Mapping[str, Any] | None,
        preferred_runtime: str,
        allowed_runtimes: tuple[str, ...],
    ) -> RuntimeSelectionProfile:
        if profile is None:
            resolved = RuntimeSelectionProfile.legacy(
                preferred_runtime=preferred_runtime,
                allowed_runtimes=allowed_runtimes,
            )
        elif isinstance(profile, RuntimeSelectionProfile):
            resolved = profile
        else:
            resolved = RuntimeSelectionProfile.from_mapping(profile)
        resolved.assert_valid()
        return resolved

    @staticmethod
    def _context(
        context: RuntimeSelectionContext | Mapping[str, Any] | None,
        plan: ExecutionPlan,
    ) -> RuntimeSelectionContext:
        if context is None:
            return RuntimeSelectionContext.from_plan(plan)
        if isinstance(context, RuntimeSelectionContext):
            return context
        value = dict(context)
        return RuntimeSelectionContext(
            required_data_locality=str(value.get("required_data_locality") or "").strip(),
            timeout_seconds=float(value.get("timeout_seconds") or plan.budget.timeout_seconds),
            max_tokens=_optional_int(value.get("max_tokens")),
            max_cost_micros=_optional_int(value.get("max_cost_micros")),
            allow_degraded_health=bool(value.get("allow_degraded_health", False)),
            policy_attributes={str(key): str(item) for key, item in dict(value.get("policy_attributes") or {}).items()},
        )

    @staticmethod
    def _audit_record(
        *,
        plan: ExecutionPlan,
        profile: RuntimeSelectionProfile,
        context: RuntimeSelectionContext,
        selected_runtime: str,
        decision_reason: str,
        mode: str,
        required: tuple[str, ...],
        evaluations: tuple[CandidateEvaluation, ...],
    ) -> RuntimeSelectionAuditRecord:
        content = {
            "tenant_id": plan.tenant_id,
            "workflow_id": plan.workflow_id,
            "plan_hash": plan.plan_hash,
            "profile": profile.to_dict(),
            "context": context.to_dict(),
            "selected_runtime": selected_runtime,
            "decision_reason": decision_reason,
            "mode": mode,
            "required": list(required),
            "evaluations": [value.to_dict() for value in evaluations],
        }
        return RuntimeSelectionAuditRecord(
            audit_id=f"rsa-{sha256_json(content)}",
            tenant_id=plan.tenant_id,
            workflow_id=plan.workflow_id,
            plan_hash=plan.plan_hash,
            profile_id=profile.profile_id,
            selected_runtime=selected_runtime,
            decision_reason_code=decision_reason,
            mode=mode,
            required_capabilities=required,
            evaluations=evaluations,
        )


class InMemoryRuntimeCatalog:
    def __init__(self, candidates: tuple[RuntimeCandidate, ...] | list[RuntimeCandidate]):
        self._candidates = tuple(candidates)

    def list_candidates(self) -> tuple[RuntimeCandidate, ...]:
        return self._candidates


class InMemoryRuntimeHealthService:
    def __init__(self, snapshots: Mapping[str, RuntimeHealthSnapshot | str]):
        self._snapshots = {
            str(runtime_id): (
                snapshot
                if isinstance(snapshot, RuntimeHealthSnapshot)
                else RuntimeHealthSnapshot(str(runtime_id), str(snapshot))
            )
            for runtime_id, snapshot in snapshots.items()
        }

    def get_health(self, runtime_id: str) -> RuntimeHealthSnapshot:
        return self._snapshots.get(
            str(runtime_id),
            RuntimeHealthSnapshot(str(runtime_id), "unavailable", "runtime_health_not_observed"),
        )


class VersionBoundRuntimePolicy:
    """Reference policy: a runtime must declare the plan policy version."""

    def evaluate(
        self,
        *,
        plan: ExecutionPlan,
        candidate: RuntimeCandidate,
        **_: Any,
    ) -> RuntimeEligibilityDecision:
        if plan.policy_version not in candidate.policy_versions and "*" not in candidate.policy_versions:
            return RuntimeEligibilityDecision(False, "runtime_policy_version_not_supported")
        return RuntimeEligibilityDecision(True, "runtime_policy_allowed")


class StrictRuntimeDataLocalityService:
    def evaluate(self, *, candidate: RuntimeCandidate, context: RuntimeSelectionContext) -> RuntimeEligibilityDecision:
        required = context.required_data_locality
        if required and required not in candidate.data_localities:
            return RuntimeEligibilityDecision(False, "runtime_data_locality_not_satisfied")
        return RuntimeEligibilityDecision(True, "runtime_data_locality_allowed")


class StrictRuntimeBudgetService:
    def evaluate(self, *, candidate: RuntimeCandidate, context: RuntimeSelectionContext) -> RuntimeEligibilityDecision:
        for name, requested, available in (
            ("timeout", context.timeout_seconds, candidate.max_timeout_seconds),
            ("tokens", context.max_tokens, candidate.max_tokens),
            ("cost", context.max_cost_micros, candidate.max_cost_micros),
        ):
            if requested is None or requested <= 0:
                continue
            if available is None:
                return RuntimeEligibilityDecision(False, f"runtime_budget_{name}_capacity_unknown")
            if requested > available:
                return RuntimeEligibilityDecision(False, f"runtime_budget_{name}_exceeded")
        return RuntimeEligibilityDecision(True, "runtime_budget_allowed")


class InMemoryRuntimeSelectionAudit:
    def __init__(self) -> None:
        self.records: list[RuntimeSelectionAuditRecord] = []

    def record(self, record: RuntimeSelectionAuditRecord) -> None:
        self.records.append(record)


class WorkflowRuntimeProfileService:
    def __init__(self, profiles: Mapping[str, RuntimeSelectionProfile]) -> None:
        self._profiles = dict(profiles)

    @classmethod
    def from_file(cls, path: str | Path) -> "WorkflowRuntimeProfileService":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if raw.get("schema") != RUNTIME_SELECTION_CATALOG_SCHEMA:
            raise ValueError("runtime_profile_catalog_schema_unsupported")
        profiles = {
            str(profile_id): RuntimeSelectionProfile.from_mapping(value, profile_id=str(profile_id))
            for profile_id, value in dict(raw.get("profiles") or {}).items()
        }
        if not profiles:
            raise ValueError("runtime_profile_catalog_empty")
        return cls(profiles)

    def resolve(self, profile_id: str) -> RuntimeSelectionProfile:
        try:
            return self._profiles[str(profile_id)]
        except KeyError as exc:
            raise KeyError("runtime_selection_profile_not_found") from exc

    def list_profiles(self) -> tuple[RuntimeSelectionProfile, ...]:
        return tuple(self._profiles[key] for key in sorted(self._profiles))


def default_workflow_runtime_profile_service() -> WorkflowRuntimeProfileService:
    path = Path(__file__).resolve().parents[2] / "config" / "workflow_runtime" / "runtime_selection_profiles.v1.json"
    return WorkflowRuntimeProfileService.from_file(path)


def _clean_tuple(values: Any) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in values or () if str(value).strip()}))


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None
