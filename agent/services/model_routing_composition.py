"""Persistence composition for Hub-owned model routing services."""

from __future__ import annotations

from collections.abc import Iterable

from agent.repositories.model_default_selection import SqlModelDefaultSelectionRepository
from agent.repositories.model_routing_configuration import SqlModelRoutingConfigurationRepository
from agent.services.model_catalog_service import (
    DefaultSelectionRuntimePort,
    ModelCatalogPort,
    ModelDefaultSelectionService,
)
from agent.services.model_profile_loader import ModelProfile
from agent.services.model_profile_resolver import ModelProfileResolver
from agent.services.model_selection_service import (
    EffectiveModelRoutingService,
    ModelConsumerRegistry,
    ModelRoutingAssignmentService,
    ModelRoutingValidationPort,
)
from ananta_contracts.model_selection import ModelRoutingConfiguration


def load_persisted_model_routing() -> ModelRoutingConfiguration:
    return SqlModelRoutingConfigurationRepository().load()


def build_persisted_model_routing_assignment_service(
    *,
    consumers: ModelConsumerRegistry,
    profiles: Iterable[ModelProfile],
    validation_policy: ModelRoutingValidationPort,
) -> ModelRoutingAssignmentService:
    known_profiles = tuple(profiles)
    return ModelRoutingAssignmentService(
        repository=SqlModelRoutingConfigurationRepository(),
        consumers=consumers,
        known_profile_ids=(profile.profile_id for profile in known_profiles),
        known_models=((profile.provider_id, profile.model) for profile in known_profiles),
        validation_policy=validation_policy,
    )


def build_persisted_effective_model_routing_service(
    *,
    consumers: ModelConsumerRegistry,
    resolver: ModelProfileResolver,
) -> EffectiveModelRoutingService:
    return EffectiveModelRoutingService(
        repository=SqlModelRoutingConfigurationRepository(),
        consumers=consumers,
        resolver=resolver,
    )


def build_persisted_default_selection_service(
    *,
    catalog: ModelCatalogPort,
    runtime: DefaultSelectionRuntimePort,
) -> ModelDefaultSelectionService:
    return ModelDefaultSelectionService(
        catalog=catalog,
        store=SqlModelDefaultSelectionRepository(),
        runtime=runtime,
    )


__all__ = [
    "build_persisted_default_selection_service",
    "build_persisted_effective_model_routing_service",
    "build_persisted_model_routing_assignment_service",
    "load_persisted_model_routing",
]
