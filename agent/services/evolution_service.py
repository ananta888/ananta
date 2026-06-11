from __future__ import annotations

import time
from typing import Any, Callable

from agent.common.audit import log_audit
from agent.metrics import (
    EVOLUTION_ANALYSES_TOTAL,
    EVOLUTION_APPLIES_TOTAL,
    EVOLUTION_VALIDATIONS_TOTAL,
)
from agent.services.evolution.context_builder import EvolutionContextBuilder, EvolutionContextBuildOptions
from agent.services.evolution.models import (
    ApplyResult,
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
from agent.services.evolution.payload_redaction import bounded_payload as _bounded_payload
from agent.services.evolution.registry import EvolutionProviderRegistry, get_evolution_provider_registry
from agent.services.evolution_proposal_service import (
    EvolutionProposalService,
    _audit_details,
    _extract_mutation_approval_artifact,
    _provider_config,
    _with_capability_matrix,
    _with_health_capability_matrix,
)
from agent.services.evolution_run_service import (
    EvolutionRunService,
    failure_audit_details,
    failure_metric_status,
    metric_label,
)
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
        self._proposal_service = EvolutionProposalService(
            repositories=repositories,
            audit_fn=self._audit_fn,
            context_builder=self._context_builder,
            validate_fn=self.validate,
            apply_fn=self.apply,
        )
        self._run_service = EvolutionRunService(
            repositories=repositories,
            audit_fn=self._audit_fn,
        )
    def _bounded_payload(self, payload: Any, *, policy: EvolutionPolicy) -> Any:
        return _bounded_payload(payload, policy=policy)
    def list_providers(self) -> list[dict[str, Any]]:
        policy = self.resolve_policy(None)
        return [
            _with_capability_matrix(item, policy=policy, provider_config_fn=_provider_config)
            for item in self._registry.list_descriptors()
        ]
    def list_providers_with_config(self, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        policy = self.resolve_policy(config)
        return [
            _with_capability_matrix(item, policy=policy, config=config, provider_config_fn=_provider_config)
            for item in self._registry.list_descriptors()
        ]
    def provider_health(self, provider_name: str | None = None) -> dict[str, Any]:
        policy = self.resolve_policy(None)
        return _with_health_capability_matrix(
            self._registry.health(provider_name), policy=policy, provider_config_fn=_provider_config,
        )
    def provider_health_with_config(self, provider_name: str | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
        policy = self.resolve_policy(config)
        return _with_health_capability_matrix(
            self._registry.health(provider_name), config=config, policy=policy, provider_config_fn=_provider_config,
        )
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
            "runs": [self._run_service.run_read_model(run) for run in runs],
            "proposals": [self._proposal_service.proposal_read_model(proposal) for proposal in proposals],
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
        return self._proposal_service.review_persisted_proposal(
            task_id, proposal_id, action=action, actor=actor, comment=comment,
        )
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
            _audit_details(engine.provider_name, context, resolved_trigger),
        )
        started_at = time.monotonic()
        try:
            result = engine.analyze(context)
            if not result.provider_name:
                result.provider_name = engine.provider_name
            persisted = self._run_service.persist_analysis(
                context, result, resolved_trigger, policy=policy, proposal_service=self._proposal_service,
            ) if persist else None
            run_id = persisted.run_id if persisted else result.run_id
            proposal_count = len(persisted.proposal_ids) if persisted else len(result.proposals)
            self._audit(
                "evolution_analysis_completed",
                {
                    **_audit_details(engine.provider_name, context, resolved_trigger),
                    "run_id": run_id,
                    "status": result.status,
                    "proposal_count": proposal_count,
                },
            )
            self._run_service.record_analysis_metrics(engine.provider_name, resolved_trigger, result)
            self._run_service.observe_operation_duration(engine.provider_name, "analyze", result.status, started_at)
            return persisted or result
        except Exception as exc:
            failure_status = failure_metric_status(exc)
            self._audit(
                "evolution_analysis_failed",
                {
                    **_audit_details(engine.provider_name, context, resolved_trigger),
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    **failure_audit_details(exc),
                },
            )
            EVOLUTION_ANALYSES_TOTAL.labels(
                provider=metric_label(engine.provider_name),
                trigger_type=metric_label(resolved_trigger.trigger_type.value),
                status=failure_status,
            ).inc()
            self._run_service.observe_operation_duration(engine.provider_name, "analyze", failure_status, started_at)
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
            **_audit_details(engine.provider_name, context, resolved_trigger),
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
                provider=metric_label(engine.provider_name),
                status=metric_label(result.status),
                valid=str(bool(result.valid)).lower(),
            ).inc()
            self._run_service.observe_operation_duration(engine.provider_name, "validate", result.status, started_at)
            return result
        except Exception as exc:
            self._audit(
                "evolution_validation_failed",
                {**details, "error": str(exc), "error_type": type(exc).__name__},
            )
            EVOLUTION_VALIDATIONS_TOTAL.labels(
                provider=metric_label(engine.provider_name),
                status="failed",
                valid="false",
            ).inc()
            self._run_service.observe_operation_duration(engine.provider_name, "validate", "failed", started_at)
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
        self._proposal_service._enforce_mutation_gate_for_apply(
            context=context,
            proposal=proposal,
            config=config,
            actor=str(resolved_trigger.actor or "system"),
            source="evolution_service.apply",
            mutation_approval_artifact=_extract_mutation_approval_artifact(proposal.provider_metadata),
            require_scoped_approval=bool(proposal.requires_review),
        )
        details = {
            **_audit_details(engine.provider_name, context, resolved_trigger),
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
                provider=metric_label(engine.provider_name),
                status=metric_label(result.status),
                applied=str(bool(result.applied)).lower(),
            ).inc()
            self._run_service.observe_operation_duration(engine.provider_name, "apply", result.status, started_at)
            return result
        except Exception as exc:
            self._audit(
                "evolution_apply_failed",
                {**details, "error": str(exc), "error_type": type(exc).__name__},
            )
            EVOLUTION_APPLIES_TOTAL.labels(
                provider=metric_label(engine.provider_name),
                status="failed",
                applied="false",
            ).inc()
            self._run_service.observe_operation_duration(engine.provider_name, "apply", "failed", started_at)
            raise
    def validate_persisted_proposal(
        self,
        task_id: str,
        proposal_id: str,
        *,
        provider_name: str | None = None,
        config: dict[str, Any] | None = None,
        trigger: EvolutionTrigger | None = None,
    ) -> ValidationResult:
        return self._proposal_service.validate_persisted_proposal(
            task_id, proposal_id, provider_name=provider_name, config=config, trigger=trigger,
        )
    def apply_persisted_proposal(
        self,
        task_id: str,
        proposal_id: str,
        *,
        provider_name: str | None = None,
        config: dict[str, Any] | None = None,
        trigger: EvolutionTrigger | None = None,
    ) -> ApplyResult:
        return self._proposal_service.apply_persisted_proposal(
            task_id, proposal_id, provider_name=provider_name, config=config, trigger=trigger,
        )
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
        provider_cfg = _provider_config(provider_name, config=config)
        if bool(provider_cfg.get("force_analyze_only", False)) and operation != "analyze":
            raise PermissionError("evolution_provider_analyze_only")

evolution_service = EvolutionService()
def get_evolution_service() -> EvolutionService:
    return evolution_service
