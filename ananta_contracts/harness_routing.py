"""Immutable model/task/scope bindings for a certified Harness route."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ananta_contracts.harness_genome import HarnessGenome, content_digest


@dataclass(frozen=True)
class HarnessRouteRequirements:
    tenant_id: str
    project_id: str
    provider_id: str
    model_id: str
    model_revision: str
    task_class: str
    hardware_profile: str
    authorized_tools: tuple[str, ...]
    context_tokens: int
    maximum_cost: float
    maximum_latency_ms: float

    def validate(self):
        identifiers = (
            self.tenant_id,
            self.project_id,
            self.provider_id,
            self.model_id,
            self.model_revision,
            self.task_class,
            self.hardware_profile,
        )
        if any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in identifiers):
            raise ValueError("harness_route_identity_required")
        if type(self.context_tokens) is not int or self.context_tokens < 1024:
            raise ValueError("harness_route_context_invalid")
        if any(
            type(v) not in {int, float} or not math.isfinite(v) or v < 0
            for v in (self.maximum_cost, self.maximum_latency_ms)
        ):
            raise ValueError("harness_route_budget_invalid")
        if len(set(self.authorized_tools)) != len(self.authorized_tools):
            raise ValueError("harness_route_tools_invalid")


@dataclass(frozen=True)
class HarnessRouteEntry:
    genome: HarnessGenome
    tenant_id: str
    project_id: str
    model_revision: str
    task_classes: tuple[str, ...]
    hardware_profiles: tuple[str, ...]
    estimated_cost: float
    estimated_latency_ms: float
    repository_revision: str
    certificate_source_id: str
    certificate_source_binding: str
    certificate_run_id: str
    certificate_task_id: str
    certificate_source_ids: tuple[str, ...]
    baseline: bool = False

    def manifest(self):
        return {
            "schema": "ananta.harness-route-binding.v1",
            "genome_id": self.genome.genome_id,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "provider_id": self.genome.model_routing.provider_id,
            "model_id": self.genome.model_routing.model_id,
            "model_revision": self.model_revision,
            "task_classes": list(self.task_classes),
            "hardware_profiles": list(self.hardware_profiles),
            "estimated_cost": self.estimated_cost,
            "estimated_latency_ms": self.estimated_latency_ms,
            "repository_revision": self.repository_revision,
            "baseline": self.baseline,
        }

    @property
    def manifest_digest(self):
        self.genome.assert_valid()
        return content_digest(self.manifest())

    def matches(self, request: HarnessRouteRequirements, *, fallback: bool = False):
        genome = self.genome
        return (
            (
                self.tenant_id,
                self.project_id,
                genome.model_routing.provider_id,
                genome.model_routing.model_id,
                self.model_revision,
            )
            == (request.tenant_id, request.project_id, request.provider_id, request.model_id, request.model_revision)
            and (request.task_class in self.task_classes or fallback and self.baseline and "*" in self.task_classes)
            and request.hardware_profile in self.hardware_profiles
            and set(genome.tools.allowed_tools) <= set(request.authorized_tools)
            and request.context_tokens <= genome.context.budget_tokens
            and type(self.estimated_cost) in {int, float}
            and math.isfinite(self.estimated_cost)
            and 0 <= self.estimated_cost <= request.maximum_cost
            and type(self.estimated_latency_ms) in {int, float}
            and math.isfinite(self.estimated_latency_ms)
            and 0 <= self.estimated_latency_ms <= request.maximum_latency_ms
        )
