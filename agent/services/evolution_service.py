from __future__ import annotations

from typing import Any, Callable

from agent.common.audit import log_audit
from agent.db_models import EvolutionProposalDB, EvolutionRunDB
from agent.services.evolution.context_builder import EvolutionContextBuilder, EvolutionContextBuildOptions
from agent.services.evolution.models import (
    ApplyResult,
    EvolutionContext,
    EvolutionProposal,
    EvolutionResult,
    EvolutionTrigger,
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
        return self._registry.list_descriptors()

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
        self._audit(
            "evolution_analysis_requested",
            self._audit_details(engine.provider_name, context, resolved_trigger),
        )
        try:
            result = engine.analyze(context)
            if not result.provider_name:
                result.provider_name = engine.provider_name
            persisted = self._persist_analysis(context, result, resolved_trigger) if persist else None
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
            return persisted or result
        except Exception as exc:
            self._audit(
                "evolution_analysis_failed",
                {
                    **self._audit_details(engine.provider_name, context, resolved_trigger),
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
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
        engine = self._registry.resolve(provider_name, config=config)
        resolved_trigger = trigger or EvolutionTrigger(trigger_type=EvolutionTriggerType.MANUAL)
        details = {
            **self._audit_details(engine.provider_name, context, resolved_trigger),
            "proposal_id": proposal.proposal_id,
        }
        self._audit("evolution_validation_requested", details)
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
            return result
        except Exception as exc:
            self._audit(
                "evolution_validation_failed",
                {**details, "error": str(exc), "error_type": type(exc).__name__},
            )
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
        engine = self._registry.resolve(provider_name, config=config)
        resolved_trigger = trigger or EvolutionTrigger(trigger_type=EvolutionTriggerType.MANUAL)
        details = {
            **self._audit_details(engine.provider_name, context, resolved_trigger),
            "proposal_id": proposal.proposal_id,
        }
        self._audit("evolution_apply_requested", details)
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
            return result
        except Exception as exc:
            self._audit(
                "evolution_apply_failed",
                {**details, "error": str(exc), "error_type": type(exc).__name__},
            )
            raise

    def _audit(self, action: str, details: dict[str, Any]) -> None:
        if self._audit_fn is not None:
            self._audit_fn(action, details)

    def _persist_analysis(
        self,
        context: EvolutionContext,
        result: EvolutionResult,
        trigger: EvolutionTrigger,
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
                provider_metadata=dict(result.provider_metadata or {}),
                raw_payload=result.raw_payload,
            )
        )
        proposal_ids: list[str] = []
        for proposal in result.proposals:
            saved = repos.evolution_proposal_repo.save(self._proposal_db(run, context, result, proposal))
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
            provider_metadata=dict(proposal.provider_metadata or {}),
            raw_payload=proposal.raw_payload,
        )

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


evolution_service = EvolutionService()


def get_evolution_service() -> EvolutionService:
    return evolution_service
