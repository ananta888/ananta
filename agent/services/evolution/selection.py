"""Pure eligibility over declared capabilities and Hub-owned engine envelopes."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from agent.services.evolution.models import EvolutionCapability, EvolutionProviderDescriptor


@dataclass(frozen=True)
class EvolutionSelectionRequest:
    model_id: str
    model_revision: str
    task_class: str
    mutation_surfaces: tuple[str, ...]
    capabilities: tuple[EvolutionCapability, ...] = (EvolutionCapability.ANALYZE, EvolutionCapability.PROPOSE)
    maximum_cost: float = 0
    maximum_model_calls: int = 0
    maximum_wall_seconds: float = 30

    def validate(self):
        if any(
            not isinstance(v, str) or not v.strip() or len(v) > 200
            for v in (self.model_id, self.model_revision, self.task_class)
        ):
            raise ValueError("evolution_selection_binding_required")
        if type(self.maximum_model_calls) is not int or self.maximum_model_calls < 0:
            raise ValueError("evolution_selection_call_budget_invalid")
        if any(
            type(v) not in {int, float} or not math.isfinite(v) or v < 0
            for v in (self.maximum_cost, self.maximum_wall_seconds)
        ):
            raise ValueError("evolution_selection_budget_invalid")
        if not self.mutation_surfaces or len(set(self.mutation_surfaces)) != len(self.mutation_surfaces):
            raise ValueError("evolution_selection_surfaces_required")


def rejection(
    descriptor: EvolutionProviderDescriptor, request: EvolutionSelectionRequest, envelope: Mapping
) -> str | None:
    if envelope.get("enabled") is not True:
        return "engine_disabled"
    if descriptor.status != "available":
        return "engine_degraded_or_unavailable"
    if not descriptor.version or descriptor.version == "unknown":
        return "engine_version_unbound"
    if envelope.get("engine_version") != descriptor.version:
        return "engine_version_mismatch"
    if not set(request.capabilities) <= set(descriptor.capabilities):
        return "engine_capability_mismatch"
    if request.model_revision not in envelope.get("model_revisions", {}).get(request.model_id, ()):
        return "engine_model_revision_mismatch"
    if request.task_class not in envelope.get("task_classes", ()):
        return "engine_task_class_mismatch"
    if not set(request.mutation_surfaces) <= set(envelope.get("mutation_surfaces", ())):
        return "engine_mutation_surface_mismatch"
    for key, maximum in (
        ("maximum_cost", request.maximum_cost),
        ("maximum_model_calls", request.maximum_model_calls),
        ("maximum_wall_seconds", request.maximum_wall_seconds),
    ):
        value = envelope.get(key)
        if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= maximum:
            return "engine_resource_envelope_mismatch"
    return None
