"""Hub runtime adapter for centrally assigned model consumers.

The assignment resolver remains the single routing authority.  This adapter only
projects its result into the small provider/model/base-url tuple required by
legacy invocation surfaces during their incremental migration.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.services.model_profile_loader import ModelProfile
from agent.services.model_selection_service import EffectiveModelRoutingService
from ananta_contracts.model_selection import ModelRoutingDryRunCommand


class ModelRuntimeSelectionError(RuntimeError):
    """Raised when an explicit central assignment cannot be executed."""


@dataclass(frozen=True, slots=True)
class ModelRuntimeSelection:
    consumer_id: str
    configuration_revision: int
    assignment_source: str
    profile_id: str
    provider_id: str
    model_id: str
    base_url: str | None


class HubModelRuntimeSelectionService:
    """Read-only bridge from Hub routing to invocation configuration."""

    def __init__(self, routing: EffectiveModelRoutingService) -> None:
        self._routing = routing

    def resolve_explicit(
        self,
        command: ModelRoutingDryRunCommand,
    ) -> ModelRuntimeSelection | None:
        route, candidates = self._routing.resolve_route(command)
        if route.assignment_mode == "inherit" and route.assignment_source == "resolver_default":
            return None
        if not route.executable:
            raise ModelRuntimeSelectionError("model_runtime_assignment_not_executable")
        profile = self._selected_profile(route.resolved_profile_id, candidates)
        if profile is None or not route.provider_id or not route.model_id:
            raise ModelRuntimeSelectionError("model_runtime_profile_not_found")
        return ModelRuntimeSelection(
            consumer_id=route.consumer_id,
            configuration_revision=route.configuration_revision,
            assignment_source=route.assignment_source,
            profile_id=profile.profile_id,
            provider_id=route.provider_id,
            model_id=route.model_id,
            base_url=profile.base_url,
        )

    @staticmethod
    def _selected_profile(
        profile_id: str | None,
        candidates: list[ModelProfile],
    ) -> ModelProfile | None:
        return next(
            (profile for profile in candidates if profile.profile_id == profile_id),
            None,
        )


def resolve_explicit_hub_model(
    command: ModelRoutingDryRunCommand,
) -> ModelRuntimeSelection | None:
    """Application composition root for Hub invocation surfaces."""

    from agent.repositories.model_routing_configuration import (
        SqlModelRoutingConfigurationRepository,
    )
    from agent.services.model_invocation_service import ModelInvocationService
    from agent.services.model_selection_service import (
        EffectiveModelRoutingService,
        ModelConsumerRegistry,
    )

    resolver = ModelInvocationService.get_profile_resolver()
    if resolver is None:
        return None
    return HubModelRuntimeSelectionService(EffectiveModelRoutingService(
        repository=SqlModelRoutingConfigurationRepository(),
        consumers=ModelConsumerRegistry.defaults(),
        resolver=resolver,
    )).resolve_explicit(command)


__all__ = [
    "HubModelRuntimeSelectionService",
    "ModelRuntimeSelection",
    "ModelRuntimeSelectionError",
    "resolve_explicit_hub_model",
]
