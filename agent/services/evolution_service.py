from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

from agent.common.audit import _sanitize_details, log_audit
from agent.db_models import EvolutionProposalDB, EvolutionRunDB
from agent.metrics import (
    EVOLUTION_ANALYSES_TOTAL,
    EVOLUTION_APPLIES_TOTAL,
    EVOLUTION_OPERATION_DURATION_SECONDS,
    EVOLUTION_PROPOSALS_TOTAL,
    EVOLUTION_VALIDATIONS_TOTAL,
)
from agent.services.evolution.context_builder import EvolutionContextBuilder, EvolutionContextBuildOptions
from agent.services.evolution.models import (
    ApplyResult,
    EvolutionCapability,
    EvolutionContext,
    EvolutionPolicy,
    EvolutionProposal,
    EvolutionResult,
    EvolutionTrigger,
    EvolutionTriggerDecision,
    EvolutionTriggerType,
    PersistedEvolutionAnalysis,
    ValidationResult,
)
from agent.services.evolution.registry import EvolutionProviderRegistry, get_evolution_provider_registry
from agent.services.repository_registry import get_repository_registry


class EvolutionService:
    """Hub-side facade that selects providers and invokes the EvolutionEngine SPI."""

    def __init__(
        self,
        *,
        registry: EvolutionProviderRegistry | None = None,
        context_builder: EvolutionContextBuilder | None = None,
        repositories=None,
        audit_fn: Callable[[str, dict], None] | None = None,
    ):
        self._registry = registry or get_evolution_provider_registry()
        self._context_builder = context_builder or EvolutionContextBuilder()
        self._repositories = repositories
        self._audit_fn = audit_fn or log_audit

    def list_providers(self) -> list[dict[str, Any]]:
        return [self._with_capability_matrix(item, config=None) for item in self._registry.list_descriptors()]

    def list_providers_with_config(self, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return [self._with_capability_matrix(item, config=config) for item in self._registry.list_descriptors()]

    def provider_health(self, provider_name: str | None = None) -> dict[str, Any]:
        return self._with_health_capability_matrix(self._registry.health(provider_name), config=None)

    def provider_health_with_config(self, provider_name: str | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._with_health_capability_matrix(self._registry.health(provider_name), config=config)

    def resolve_policy(self, config: dict[str, Any] | None = None) -> EvolutionPolicy:
        raw = dict((config or {}).get("evolution") or config or {})
        return EvolutionPolicy(
            enabled=bool(raw.get("enabled", True)),
            analyze_only=bool(raw.get("analyze_only", True)),
            validate_allowed=bool(raw.get("validate_allowed", True)),
            apply_allowed=bool(raw.get("apply_allowed", False)),
            auto_triggers_enabled=bool(raw.get("auto_triggers_enabled", False)),
            manual_triggers_enabled=bool(raw.get("manual_triggers_enabled", True)),
            require_review_before_apply=bool(raw.get("require_review_before_apply", True)),
            max_raw_payload_bytes=max(1024, min(int(raw.get("max_raw_payload_bytes") or 32768), 1024 * 1024)),
            max_manual_analyses_per_task=max(1, min(int(raw.get("max_manual_analyses_per_task") or 20), 1000)),
        )

    def evaluate_auto_trigger(
        self,
        task: dict[str, Any],
        *,
        config: dict[str, Any] | None = None,
    ) -> EvolutionTriggerDecision:
        policy = self.resolve_policy(config)
        if not policy.enabled:
            return EvolutionTriggerDecision(allowed=False, reasons=["evolution_disabled"])
        if not policy.auto_triggers_enabled:
            return EvolutionTriggerDecision(allowed=False, reasons=["auto_triggers_disabled"])

        status = str(task.get("status") or "").strip().lower()
        verification_status = dict(task.get("verification_status") or {})
        verification_failed = str(verification_status.get("status") or "").strip().lower() in {"failed", "escalated"}
        task_failed = status in {"failed", "blocked"}
        if verification_failed or task_failed:
            return EvolutionTriggerDecision(
                allowed=True,
                trigger=EvolutionTrigger(
                    trigger_type=EvolutionTriggerType.VERIFICATION_FAILURE,
                    source="task_policy",
                    reason="verification_or_task_failure",
                    trigger_metadata={"task_status": status, "verification_status": verification_status.get("status")},
                ),
                reasons=["verification_or_task_failure"],
                details={"task_status": status, "verification_status": verification_status.get("status")},
            )
        return EvolutionTriggerDecision(
            allowed=False,
            reasons=["no_matching_trigger_condition"],
            details={"task_status": status, "verification_status": verification_status.get("status")},
        )

    def task_read_model(self, task_id: str, *, limit: int = 50) -> dict[str, Any]:
        repos = self._repositories or get_repository_registry()
        task = repos.task_repo.get_by_id(task_id)
        if task is None:
            raise KeyError("task_not_found")
        runs = list(repos.evolution_run_repo.get_by_task_id(task_id, limit=limit))
        proposals = list(repos.evolution_proposal_repo.get_by_task_id(task_id, limit=limit))
        return {
            "task_id": task_id,
            "run_count": len(runs),
            "proposal_count": len(proposals),
            "runs": [self._run_read_model(run) for run in runs],
            "proposals": [self._proposal_read_model(proposal) for proposal in proposals],
        }

    def review_persisted_proposal(
        self,
        task_id: str,
        proposal_id: str,
        *,
        action: str,
        actor: str,
        comment: str | None = None,
    ) -> dict[str, Any]:
        repos = self._repositories or get_repository_registry()
        persisted = repos.evolution_proposal_repo.get_by_id(proposal_id)
        if persisted is None or str(persisted.task_id or "") != str(task_id):
            raise KeyError("evolution_proposal_not_found")
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"approve", "reject"}:
            raise ValueError("invalid_review_action")

        review_status = "approved" if normalized_action == "approve" else "rejected"
        proposal_metadata = dict(persisted.proposal_metadata or {})
        history = list(proposal_metadata.get("history") or [])
        review = {
            "required": bool(persisted.requires_review),
            "status": review_status,
            "reviewed_by": str(actor or "system"),
            "reviewed_at": time.time(),
            "comment": str(comment or "").strip() or None,
        }
        history.append(
            {
                "event_type": "proposal_review",
                "action": normalized_action,
                "status": review_status,
                "actor": review["reviewed_by"],
                "comment": review["comment"],
                "timestamp": review["reviewed_at"],
            }
        )
        proposal_metadata["review"] = review
        proposal_metadata["history"] = history[-20:]
        persisted.proposal_metadata = proposal_metadata
        persisted.status = "approved" if review_status == "approved" else "rejected"
        saved = repos.evolution_proposal_repo.save(persisted)
        self._audit(
            "evolution_proposal_reviewed",
            {
                "task_id": task_id,
                "proposal_id": proposal_id,
                "action": normalized_action,
                "status": review_status,
                "actor": review["reviewed_by"],
            },
        )
        return self._proposal_read_model(saved)

    def build_context_for_task(
        self,
        task_id: str,
        *,
        objective: str | None = None,
        options: EvolutionContextBuildOptions | None = None,
    ) -> EvolutionContext:
        return self._context_builder.build_for_task(task_id, objective=objective, options=options)

    def analyze_task(
        self,
        task_id: str,
        *,
        objective: str | None = None,
        provider_name: str | None = None,
        config: dict[str, Any] | None = None,
        options: EvolutionContextBuildOptions | None = None,
        trigger: EvolutionTrigger | None = None,
        persist: bool = True,
    ) -> PersistedEvolutionAnalysis | EvolutionResult:
        context = self.build_context_for_task(task_id, objective=objective, options=options)
        return self.analyze(context, provider_name=provider_name, config=config, trigger=trigger, persist=persist)

    def analyze(
        self,
        context: EvolutionContext,
        *,
        provider_name: str | None = None,
        config: dict[str, Any] | None = None,
        trigger: EvolutionTrigger | None = None,
        persist: bool = False,
    ) -> PersistedEvolutionAnalysis | EvolutionResult:
        engine = self._registry.resolve(provider_name, config=config)
        resolved_trigger = trigger or EvolutionTrigger(trigger_type=EvolutionTriggerType.MANUAL)
        policy = self.resolve_policy(config)
        if not policy.enabled:
            raise PermissionError("evolution_disabled")
        self._enforce_provider_policy(engine.provider_name, config=config, operation="analyze")
        self._audit(
            "evolution_analysis_requested",
            self._audit_details(engine.provider_name, context, resolved_trigger),
        )
        started_at = time.monotonic()
        try:
            result = engine.analyze(context)
            if not result.provider_name:
                result.provider_name = engine.provider_name
            persisted = self._persist_analysis(context, result, resolved_trigger, policy=policy) if persist else None
            run_id = persisted.run_id if persisted else result.run_id
            proposal_count = len(persisted.proposal_ids) if persisted else len(result.proposals)
            self._audit(
                "evolution_analysis_completed",
                {
                    **self._audit_details(engine.provider_name, context, resolved_trigger),
                    "run_id": run_id,
                    "status": result.status,
                    "proposal_count": proposal_count,
                },
            )
            self._record_analysis_metrics(engine.provider_name, resolved_trigger, result)
            self._observe_operation_duration(engine.provider_name, "analyze", result.status, started_at)
            return persisted or result
        except Exception as exc:
            failure_status = self._failure_metric_status(exc)
            self._audit(
                "evolution_analysis_failed",
                {
                    **self._audit_details(engine.provider_name, context, resolved_trigger),
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    **self._failure_audit_details(exc),
                },
            )
            EVOLUTION_ANALYSES_TOTAL.labels(
                provider=self._metric_label(engine.provider_name),
                trigger_type=self._metric_label(resolved_trigger.trigger_type.value),
                status=failure_status,
            ).inc()
            self._observe_operation_duration(engine.provider_name, "analyze", failure_status, started_at)
            raise

    def validate(
        self,
        context: EvolutionContext,
        proposal: EvolutionProposal,
        *,
        provider_name: str | None = None,
        config: dict[str, Any] | None = None,
        trigger: EvolutionTrigger | None = None,
    ) -> ValidationResult:
        policy = self.resolve_policy(config)
        if not policy.validate_allowed:
            raise PermissionError("evolution_validation_disabled")
        engine = self._registry.resolve(provider_name, config=config)
        self._enforce_provider_policy(engine.provider_name, config=config, operation="validate")
        resolved_trigger = trigger or EvolutionTrigger(trigger_type=EvolutionTriggerType.MANUAL)
        details = {
            **self._audit_details(engine.provider_name, context, resolved_trigger),
            "proposal_id": proposal.proposal_id,
        }
        self._audit("evolution_validation_requested", details)
        started_at = time.monotonic()
        try:
            result = engine.validate(context, proposal)
            self._audit(
                "evolution_validation_completed",
                {
                    **details,
                    "validation_id": result.validation_id,
                    "status": result.status,
                    "valid": result.valid,
                },
            )
            EVOLUTION_VALIDATIONS_TOTAL.labels(
                provider=self._metric_label(engine.provider_name),
                status=self._metric_label(result.status),
                valid=str(bool(result.valid)).lower(),
            ).inc()
            self._observe_operation_duration(engine.provider_name, "validate", result.status, started_at)
            return result
        except Exception as exc:
            self._audit(
                "evolution_validation_failed",
                {**details, "error": str(exc), "error_type": type(exc).__name__},
            )
            EVOLUTION_VALIDATIONS_TOTAL.labels(
                provider=self._metric_label(engine.provider_name),
                status="failed",
                valid="false",
            ).inc()
            self._observe_operation_duration(engine.provider_name, "validate", "failed", started_at)
            raise

    def apply(
        self,
        context: EvolutionContext,
        proposal: EvolutionProposal,
        *,
        provider_name: str | None = None,
        config: dict[str, Any] | None = None,
        trigger: EvolutionTrigger | None = None,
    ) -> ApplyResult:
        policy = self.resolve_policy(config)
        if not policy.apply_allowed:
            raise PermissionError("evolution_apply_disabled")
        if policy.require_review_before_apply and proposal.requires_review:
            raise PermissionError("evolution_apply_requires_review")
        engine = self._registry.resolve(provider_name, config=config)
        self._enforce_provider_policy(engine.provider_name, config=config, operation="apply")
        resolved_trigger = trigger or EvolutionTrigger(trigger_type=EvolutionTriggerType.MANUAL)
        details = {
            **self._audit_details(engine.provider_name, context, resolved_trigger),
            "proposal_id": proposal.proposal_id,
        }
        self._audit("evolution_apply_requested", details)
        started_at = time.monotonic()
        try:
            result = engine.apply(context, proposal)
            self._audit(
                "evolution_apply_completed",
                {
                    **details,
                    "apply_id": result.apply_id,
                    "status": result.status,
                    "applied": result.applied,
                },
            )
            EVOLUTION_APPLIES_TOTAL.labels(
                provider=self._metric_label(engine.provider_name),
                status=self._metric_label(result.status),
                applied=str(bool(result.applied)).lower(),
            ).inc()
            self._observe_operation_duration(engine.provider_name, "apply", result.status, started_at)
            return result
        except Exception as exc:
            self._audit(
                "evolution_apply_failed",
                {**details, "error": str(exc), "error_type": type(exc).__name__},
            )
            EVOLUTION_APPLIES_TOTAL.labels(
                provider=self._metric_label(engine.provider_name),
                status="failed",
                applied="false",
            ).inc()
            self._observe_operation_duration(engine.provider_name, "apply", "failed", started_at)
            raise

    def _audit(self, action: str, details: dict[str, Any]) -> None:
        if self._audit_fn is not None:
            self._audit_fn(action, details)

    def _enforce_provider_policy(
        self,
        provider_name: str,
        *,
        config: dict[str, Any] | None,
        operation: str,
    ) -> None:
        provider_cfg = self._provider_config(provider_name, config=config)
        if bool(provider_cfg.get("force_analyze_only", False)) and operation != "analyze":
            raise PermissionError("evolution_provider_analyze_only")

    def _with_health_capability_matrix(self, health: dict[str, Any], *, config: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(health or {})
        payload["providers"] = [
            self._with_capability_matrix(dict(provider), config=config)
            for provider in payload.get("providers", [])
            if isinstance(provider, dict)
        ]
        return payload

    def _with_capability_matrix(self, descriptor: dict[str, Any], *, config: dict[str, Any] | None) -> dict[str, Any]:
        provider_name = str(descriptor.get("provider_name") or "").strip()
        capabilities = set()
        for item in descriptor.get("capabilities") or []:
            try:
                capabilities.add(EvolutionCapability(str(item)))
            except ValueError:
                continue
        policy = self.resolve_policy(config)
        provider_cfg = self._provider_config(provider_name, config=config)
        force_analyze_only = bool(provider_cfg.get("force_analyze_only", False))
        matrix = {
            "analyze": self._capability_entry(
                EvolutionCapability.ANALYZE,
                supported=EvolutionCapability.ANALYZE in capabilities,
                policy_allowed=policy.enabled,
                configured=True,
            ),
            "propose": self._capability_entry(
                EvolutionCapability.PROPOSE,
                supported=EvolutionCapability.PROPOSE in capabilities,
                policy_allowed=policy.enabled,
                configured=True,
                note="Analyze may still return reviewable proposals when provider supports provider-specific proposal output.",
            ),
            "validate": self._capability_entry(
                EvolutionCapability.VALIDATE,
                supported=EvolutionCapability.VALIDATE in capabilities,
                policy_allowed=policy.enabled and policy.validate_allowed and not force_analyze_only,
                configured=not force_analyze_only,
                fail_closed_reason="evolution_provider_analyze_only" if force_analyze_only else None,
            ),
            "apply": self._capability_entry(
                EvolutionCapability.APPLY,
                supported=EvolutionCapability.APPLY in capabilities,
                policy_allowed=policy.enabled and policy.apply_allowed and not force_analyze_only,
                configured=not force_analyze_only,
                fail_closed_reason=(
                    "evolution_provider_analyze_only"
                    if force_analyze_only
                    else ("evolution_apply_disabled" if not policy.apply_allowed else None)
                ),
            ),
            "risk_scoring": self._capability_entry(
                EvolutionCapability.RISK_SCORING,
                supported=EvolutionCapability.RISK_SCORING in capabilities,
                policy_allowed=policy.enabled,
                configured=True,
            ),
            "review_hints": self._capability_entry(
                EvolutionCapability.REVIEW_HINTS,
                supported=EvolutionCapability.REVIEW_HINTS in capabilities,
                policy_allowed=policy.enabled,
                configured=True,
            ),
        }
        enriched = dict(descriptor)
        enriched["capability_matrix"] = matrix
        enriched["fail_closed"] = any(
            entry["supported"] and not entry["available"]
            for entry in matrix.values()
        )
        return enriched

    @staticmethod
    def _capability_entry(
        capability: EvolutionCapability,
        *,
        supported: bool,
        policy_allowed: bool,
        configured: bool,
        fail_closed_reason: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        available = bool(supported and policy_allowed and configured)
        payload = {
            "capability": capability.value,
            "supported": bool(supported),
            "policy_allowed": bool(policy_allowed),
            "configured": bool(configured),
            "available": available,
        }
        if fail_closed_reason and not available:
            payload["fail_closed_reason"] = fail_closed_reason
        if note:
            payload["note"] = note
        return payload

    @staticmethod
    def _provider_config(provider_name: str, *, config: dict[str, Any] | None) -> dict[str, Any]:
        raw = dict((config or {}).get("evolution") or config or {})
        overrides = raw.get("provider_overrides") if isinstance(raw.get("provider_overrides"), dict) else {}
        provider_cfg = overrides.get(str(provider_name or "").strip().lower())
        return dict(provider_cfg or {})

    def _record_analysis_metrics(
        self,
        provider_name: str,
        trigger: EvolutionTrigger,
        result: EvolutionResult,
    ) -> None:
        EVOLUTION_ANALYSES_TOTAL.labels(
            provider=self._metric_label(provider_name),
            trigger_type=self._metric_label(trigger.trigger_type.value),
            status=self._metric_label(result.status),
        ).inc()
        for proposal in result.proposals:
            EVOLUTION_PROPOSALS_TOTAL.labels(
                provider=self._metric_label(provider_name),
                proposal_type=self._metric_label(proposal.proposal_type),
                risk_level=self._metric_label(proposal.risk_level),
                requires_review=str(bool(proposal.requires_review)).lower(),
            ).inc()

    def _observe_operation_duration(
        self,
        provider_name: str,
        operation: str,
        status: str,
        started_at: float,
    ) -> None:
        EVOLUTION_OPERATION_DURATION_SECONDS.labels(
            provider=self._metric_label(provider_name),
            operation=self._metric_label(operation),
            status=self._metric_label(status),
        ).observe(max(0.0, time.monotonic() - started_at))

    @staticmethod
    def _metric_label(value: Any) -> str:
        text = str(value or "unknown").strip().lower()
        return text[:80] or "unknown"

    @classmethod
    def _failure_metric_status(cls, exc: Exception) -> str:
        code = getattr(exc, "code", None)
        if code:
            return cls._metric_label(f"failed_{code}")
        return "failed"

    @staticmethod
    def _failure_audit_details(exc: Exception) -> dict[str, Any]:
        details: dict[str, Any] = {}
        code = getattr(exc, "code", None)
        if code:
            details["error_code"] = str(code)
        if hasattr(exc, "transient"):
            details["transient"] = bool(getattr(exc, "transient"))
        status_code = getattr(exc, "status_code", None)
        if status_code is not None:
            details["status_code"] = status_code
        return details

    def _persist_analysis(
        self,
        context: EvolutionContext,
        result: EvolutionResult,
        trigger: EvolutionTrigger,
        *,
        policy: EvolutionPolicy,
    ) -> PersistedEvolutionAnalysis:
        repos = self._repositories or get_repository_registry()
        run = repos.evolution_run_repo.save(
            EvolutionRunDB(
                id=result.run_id,
                provider_name=result.provider_name,
                status=result.status,
                trigger_type=trigger.trigger_type.value,
                trigger_source=trigger.source,
                task_id=context.task_id,
                goal_id=context.goal_id,
                trace_id=context.trace_id,
                plan_id=context.plan_id,
                context_id=context.context_id,
                summary=result.summary,
                context_refs=list(context.source_refs or []),
                result_metadata={
                    "proposal_count": len(result.proposals),
                    "validation_count": len(result.validation_results),
                    "trigger": trigger.model_dump(mode="json"),
                },
                provider_metadata=self._bounded_payload(result.provider_metadata or {}, policy=policy),
                raw_payload=self._bounded_payload(result.raw_payload, policy=policy),
            )
        )
        proposal_ids: list[str] = []
        for proposal in result.proposals:
            saved = repos.evolution_proposal_repo.save(self._proposal_db(run, context, result, proposal, policy=policy))
            proposal_ids.append(saved.id)
        return PersistedEvolutionAnalysis(
            run_id=run.id,
            provider_name=run.provider_name,
            status=run.status,
            proposal_ids=proposal_ids,
            result=result,
        )

    def _proposal_db(
        self,
        run: EvolutionRunDB,
        context: EvolutionContext,
        result: EvolutionResult,
        proposal: EvolutionProposal,
        *,
        policy: EvolutionPolicy,
    ) -> EvolutionProposalDB:
        return EvolutionProposalDB(
            id=proposal.proposal_id,
            run_id=run.id,
            provider_name=result.provider_name,
            task_id=context.task_id,
            goal_id=context.goal_id,
            trace_id=context.trace_id,
            proposal_type=proposal.proposal_type,
            title=proposal.title,
            description=proposal.description,
            rationale=proposal.rationale,
            risk_level=proposal.risk_level,
            confidence=proposal.confidence,
            requires_review=proposal.requires_review,
            target_refs=list(proposal.target_refs or []),
            proposal_metadata={"context_id": context.context_id},
            provider_metadata=self._bounded_payload(proposal.provider_metadata or {}, policy=policy),
            raw_payload=self._bounded_payload(proposal.raw_payload, policy=policy),
        )

    def validate_persisted_proposal(
        self,
        task_id: str,
        proposal_id: str,
        *,
        provider_name: str | None = None,
        config: dict[str, Any] | None = None,
        trigger: EvolutionTrigger | None = None,
    ) -> ValidationResult:
        repos = self._repositories or get_repository_registry()
        persisted = repos.evolution_proposal_repo.get_by_id(proposal_id)
        if persisted is None or str(persisted.task_id or "") != str(task_id):
            raise KeyError("evolution_proposal_not_found")
        context = self.build_context_for_task(task_id)
        proposal = EvolutionProposal(
            proposal_id=persisted.id,
            title=persisted.title,
            description=persisted.description,
            proposal_type=persisted.proposal_type,
            target_refs=list(persisted.target_refs or []),
            rationale=persisted.rationale,
            risk_level=persisted.risk_level,
            confidence=persisted.confidence,
            requires_review=persisted.requires_review,
            provider_metadata=dict(persisted.provider_metadata or {}),
            raw_payload=persisted.raw_payload,
        )
        result = self.validate(
            context,
            proposal,
            provider_name=provider_name or persisted.provider_name,
            config=config,
            trigger=trigger,
        )
        proposal_metadata = dict(persisted.proposal_metadata or {})
        history = list(proposal_metadata.get("history") or [])
        validations = list(proposal_metadata.get("validations") or [])
        validation_entry = result.model_dump(mode="json")
        validations.append(validation_entry)
        history.append(
            {
                "event_type": "proposal_validation",
                "status": result.status,
                "valid": bool(result.valid),
                "validation_id": result.validation_id,
                "timestamp": time.time(),
            }
        )
        proposal_metadata["last_validation"] = validation_entry
        proposal_metadata["validations"] = validations[-20:]
        proposal_metadata["history"] = history[-20:]
        persisted.proposal_metadata = proposal_metadata
        persisted.status = "validated" if result.valid else "validation_failed"
        repos.evolution_proposal_repo.save(persisted)
        return result

    def apply_persisted_proposal(
        self,
        task_id: str,
        proposal_id: str,
        *,
        provider_name: str | None = None,
        config: dict[str, Any] | None = None,
        trigger: EvolutionTrigger | None = None,
    ) -> ApplyResult:
        repos = self._repositories or get_repository_registry()
        persisted = repos.evolution_proposal_repo.get_by_id(proposal_id)
        if persisted is None or str(persisted.task_id or "") != str(task_id):
            raise KeyError("evolution_proposal_not_found")
        proposal_metadata = dict(persisted.proposal_metadata or {})
        review = dict(proposal_metadata.get("review") or {})
        if bool(persisted.requires_review) and str(review.get("status") or "").strip().lower() != "approved":
            raise PermissionError("evolution_apply_requires_approved_review")
        context = self.build_context_for_task(task_id)
        proposal = EvolutionProposal(
            proposal_id=persisted.id,
            title=persisted.title,
            description=persisted.description,
            proposal_type=persisted.proposal_type,
            target_refs=list(persisted.target_refs or []),
            rationale=persisted.rationale,
            risk_level=persisted.risk_level,
            confidence=persisted.confidence,
            requires_review=persisted.requires_review,
            provider_metadata=dict(persisted.provider_metadata or {}),
            raw_payload=persisted.raw_payload,
        )
        result = self.apply(
            context,
            proposal,
            provider_name=provider_name or persisted.provider_name,
            config=config,
            trigger=trigger,
        )
        applies = list(proposal_metadata.get("applies") or [])
        history = list(proposal_metadata.get("history") or [])
        apply_entry = result.model_dump(mode="json")
        applies.append(apply_entry)
        history.append(
            {
                "event_type": "proposal_apply",
                "status": result.status,
                "applied": bool(result.applied),
                "apply_id": result.apply_id,
                "timestamp": time.time(),
            }
        )
        rollback_hints = list(proposal_metadata.get("rollback_hints") or [])
        if not rollback_hints:
            rollback_hints = [
                "Apply bleibt hub-gesteuert; pruefe Audit-Trace, Artefakt-Referenzen und betroffene Targets vor Rollback.",
                "Rollback darf keine Worker-zu-Worker-Orchestrierung ausloesen.",
            ]
        proposal_metadata["last_apply"] = apply_entry
        proposal_metadata["applies"] = applies[-20:]
        proposal_metadata["history"] = history[-20:]
        proposal_metadata["rollback_hints"] = rollback_hints
        persisted.proposal_metadata = proposal_metadata
        persisted.artifact_refs = list(result.artifact_refs or [])
        persisted.status = "applied" if result.applied else "apply_prepared"
        repos.evolution_proposal_repo.save(persisted)
        return result

    def _audit_details(
        self,
        provider_name: str,
        context: EvolutionContext,
        trigger: EvolutionTrigger,
    ) -> dict[str, Any]:
        return {
            "provider_name": provider_name,
            "task_id": context.task_id,
            "goal_id": context.goal_id,
            "trace_id": context.trace_id,
            "plan_id": context.plan_id,
            "trigger_type": trigger.trigger_type.value,
            "trigger_source": trigger.source,
            "actor": trigger.actor,
            "reason": trigger.reason,
        }

    def _bounded_payload(self, payload: Any, *, policy: EvolutionPolicy) -> Any:
        if payload is None:
            return None
        sanitized = self._sanitize_persisted_payload(payload)
        try:
            encoded = json.dumps(sanitized, ensure_ascii=True, sort_keys=True, default=str)
        except TypeError:
            sanitized = {"value": str(sanitized)}
            encoded = json.dumps(sanitized, ensure_ascii=True, sort_keys=True)
        if len(encoded.encode("utf-8")) <= policy.max_raw_payload_bytes:
            return sanitized
        semantic_preview = self._semantic_payload_preview(sanitized)
        if semantic_preview:
            return {
                "_truncated": True,
                "max_raw_payload_bytes": policy.max_raw_payload_bytes,
                **semantic_preview,
            }
        preview = encoded[: policy.max_raw_payload_bytes]
        return {
            "_truncated": True,
            "max_raw_payload_bytes": policy.max_raw_payload_bytes,
            "preview": preview,
        }

    @classmethod
    def _semantic_payload_preview(cls, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        keep_keys = (
            "run_id",
            "id",
            "status",
            "summary",
            "message",
            "source",
            "evolver_run_id",
            "evolver_status",
        )
        preview = {key: payload[key] for key in keep_keys if key in payload}
        for key in ("proposals", "improvements", "candidates", "events", "validation_results", "validations"):
            value = payload.get(key)
            if isinstance(value, list):
                preview[f"{key}_count"] = len(value)
        if preview:
            preview["preview_keys"] = sorted(str(key) for key in payload.keys())[:30]
        return {"semantic_preview": preview} if preview else {}

    @classmethod
    def _sanitize_persisted_payload(cls, payload: Any) -> Any:
        sanitized = _sanitize_details(payload) if isinstance(payload, dict) else payload
        return cls._redact_persisted_value(sanitized)

    @classmethod
    def _redact_persisted_value(cls, value: Any) -> Any:
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if cls._is_sensitive_persisted_key(key_text):
                    redacted[key] = "***REDACTED***"
                else:
                    redacted[key] = cls._redact_persisted_value(item)
            return redacted
        if isinstance(value, list):
            return [cls._redact_persisted_value(item) for item in value]
        if isinstance(value, str):
            return cls._redact_persisted_text(value)
        return value

    @staticmethod
    def _is_sensitive_persisted_key(key: str) -> bool:
        normalized = key.strip().lower().replace("-", "_")
        sensitive_fragments = ("token", "secret", "password", "api_key", "apikey", "credential", "authorization")
        if any(fragment in normalized for fragment in sensitive_fragments):
            return True
        return normalized in {"headers", "request_headers", "response_headers", "auth"}

    @staticmethod
    def _redact_persisted_text(value: str) -> str:
        redacted = value
        redacted = re.sub(
            r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|host\.docker\.internal)(?::\d+)?[^\s,\]\)\"]*",
            "***REDACTED_LOCAL_URL***",
            redacted,
            flags=re.IGNORECASE,
        )
        redacted = re.sub(
            r"(?<![\w.:-])(?:/[A-Za-z0-9._ -]+){2,}",
            "***REDACTED_PATH***",
            redacted,
        )
        redacted = re.sub(
            r"(?<![\w.-])[A-Za-z]:\\(?:[^\\/:*?\"<>|\r\n]+\\?){2,}",
            "***REDACTED_PATH***",
            redacted,
        )
        return redacted

    def _run_read_model(self, run: EvolutionRunDB) -> dict[str, Any]:
        return {
            "run_id": run.id,
            "provider_name": run.provider_name,
            "status": run.status,
            "trigger_type": run.trigger_type,
            "trigger_source": run.trigger_source,
            "task_id": run.task_id,
            "goal_id": run.goal_id,
            "trace_id": run.trace_id,
            "plan_id": run.plan_id,
            "summary": run.summary,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "result_metadata": dict(run.result_metadata or {}),
            "provider_metadata": dict(run.provider_metadata or {}),
        }

    def _proposal_read_model(self, proposal: EvolutionProposalDB) -> dict[str, Any]:
        proposal_metadata = dict(proposal.proposal_metadata or {})
        review = dict(proposal_metadata.get("review") or {})
        validations = list(proposal_metadata.get("validations") or [])
        applies = list(proposal_metadata.get("applies") or [])
        return {
            "proposal_id": proposal.id,
            "run_id": proposal.run_id,
            "provider_name": proposal.provider_name,
            "proposal_type": proposal.proposal_type,
            "title": proposal.title,
            "description": proposal.description,
            "rationale": proposal.rationale,
            "risk_level": proposal.risk_level,
            "confidence": proposal.confidence,
            "requires_review": proposal.requires_review,
            "status": proposal.status,
            "target_refs": list(proposal.target_refs or []),
            "artifact_refs": list(proposal.artifact_refs or []),
            "review": {
                "required": bool(proposal.requires_review),
                "status": str(review.get("status") or ("pending" if proposal.requires_review else "not_required")),
                "reviewed_by": review.get("reviewed_by"),
                "reviewed_at": review.get("reviewed_at"),
                "comment": review.get("comment"),
            },
            "validation_summary": {
                "count": len(validations),
                "last_result": proposal_metadata.get("last_validation"),
            },
            "apply_summary": {
                "count": len(applies),
                "last_result": proposal_metadata.get("last_apply"),
                "rollback_hints": list(proposal_metadata.get("rollback_hints") or []),
            },
            "history": list(proposal_metadata.get("history") or []),
            "provider_metadata": dict(proposal.provider_metadata or {}),
            "proposal_metadata": proposal_metadata,
            "created_at": proposal.created_at,
            "updated_at": proposal.updated_at,
        }


evolution_service = EvolutionService()


def get_evolution_service() -> EvolutionService:
    return evolution_service
