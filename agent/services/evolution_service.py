from __future__ import annotations

from typing import Any, Callable

from agent.services.evolution.models import (
    ApplyResult,
    EvolutionContext,
    EvolutionProposal,
    EvolutionResult,
    ValidationResult,
)
from agent.services.evolution.registry import EvolutionProviderRegistry, get_evolution_provider_registry


class EvolutionService:
    """Hub-side facade that selects providers and invokes the EvolutionEngine SPI."""

    def __init__(
        self,
        *,
        registry: EvolutionProviderRegistry | None = None,
        audit_fn: Callable[[str, dict], None] | None = None,
    ):
        self._registry = registry or get_evolution_provider_registry()
        self._audit_fn = audit_fn

    def list_providers(self) -> list[dict[str, Any]]:
        return self._registry.list_descriptors()

    def analyze(
        self,
        context: EvolutionContext,
        *,
        provider_name: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> EvolutionResult:
        engine = self._registry.resolve(provider_name, config=config)
        result = engine.analyze(context)
        if not result.provider_name:
            result.provider_name = engine.provider_name
        self._audit(
            "evolution_analysis_completed",
            {
                "provider_name": engine.provider_name,
                "run_id": result.run_id,
                "task_id": context.task_id,
                "goal_id": context.goal_id,
                "trace_id": context.trace_id,
                "proposal_count": len(result.proposals),
            },
        )
        return result

    def validate(
        self,
        context: EvolutionContext,
        proposal: EvolutionProposal,
        *,
        provider_name: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> ValidationResult:
        engine = self._registry.resolve(provider_name, config=config)
        result = engine.validate(context, proposal)
        self._audit(
            "evolution_validation_completed",
            {
                "provider_name": engine.provider_name,
                "proposal_id": proposal.proposal_id,
                "validation_id": result.validation_id,
                "status": result.status,
                "valid": result.valid,
                "task_id": context.task_id,
                "goal_id": context.goal_id,
                "trace_id": context.trace_id,
            },
        )
        return result

    def apply(
        self,
        context: EvolutionContext,
        proposal: EvolutionProposal,
        *,
        provider_name: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> ApplyResult:
        engine = self._registry.resolve(provider_name, config=config)
        result = engine.apply(context, proposal)
        self._audit(
            "evolution_apply_completed",
            {
                "provider_name": engine.provider_name,
                "proposal_id": proposal.proposal_id,
                "apply_id": result.apply_id,
                "status": result.status,
                "applied": result.applied,
                "task_id": context.task_id,
                "goal_id": context.goal_id,
                "trace_id": context.trace_id,
            },
        )
        return result

    def _audit(self, action: str, details: dict[str, Any]) -> None:
        if self._audit_fn is not None:
            self._audit_fn(action, details)


evolution_service = EvolutionService()


def get_evolution_service() -> EvolutionService:
    return evolution_service
