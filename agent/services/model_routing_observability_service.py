"""Secret-free central model routing usage and diagnostic projections."""

from __future__ import annotations

import threading
from collections import Counter
from datetime import UTC, datetime
from typing import Iterable

from ananta_contracts.model_catalog import ModelCatalogV2
from ananta_contracts.model_selection import (
    EffectiveModelRoute,
    ModelConsumer,
    ModelRoutingConfiguration,
    ModelRoutingDiagnosticIssue,
    ModelRoutingDiagnostics,
    ModelRoutingUsageAggregate,
)


class ModelRoutingUsageProjection:
    """Bounded process-local aggregation; it never stores prompts or tokens."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Counter[tuple[str, str]] = Counter()
        self._fallbacks: Counter[tuple[str, str]] = Counter()
        self._last_used: dict[tuple[str, str], str] = {}

    def record(self, route: EffectiveModelRoute) -> None:
        if not route.executable or not route.resolved_profile_id:
            return
        key = (route.consumer_id, route.resolved_profile_id)
        fallback = bool(
            route.candidate_profile_ids
            and route.candidate_profile_ids[0] != route.resolved_profile_id
        )
        with self._lock:
            self._counts[key] += 1
            self._fallbacks[key] += int(fallback)
            self._last_used[key] = _now()
        from agent import metrics

        metrics.MODEL_ROUTING_DECISIONS_TOTAL.labels(
            outcome="fallback" if fallback else "primary"
        ).inc()

    def read(self) -> tuple[ModelRoutingUsageAggregate, ...]:
        with self._lock:
            return tuple(
                ModelRoutingUsageAggregate(
                    consumer_id=consumer_id,
                    profile_id=profile_id,
                    selections_total=self._counts[(consumer_id, profile_id)],
                    fallback_selections_total=self._fallbacks[(consumer_id, profile_id)],
                    last_used_at=self._last_used[(consumer_id, profile_id)],
                )
                for consumer_id, profile_id in sorted(self._counts)
            )


class ModelRoutingDiagnosticsService:
    def build(
        self,
        *,
        configuration: ModelRoutingConfiguration,
        catalog: ModelCatalogV2,
        consumers: Iterable[ModelConsumer],
        effective_routes: Iterable[EffectiveModelRoute],
        known_profile_ids: Iterable[str],
        usage: Iterable[ModelRoutingUsageAggregate],
    ) -> ModelRoutingDiagnostics:
        consumer_values = tuple(consumers)
        routes = tuple(effective_routes)
        known_consumers = {item.consumer_id for item in consumer_values}
        known_profiles = set(known_profile_ids)
        known_models = {
            (item.provider_id, item.model_id) for item in catalog.models
        }
        issues: list[ModelRoutingDiagnosticIssue] = []
        for assignment in configuration.assignments:
            key = f"{assignment.consumer_id}:{assignment.scope}:{assignment.scope_id}"
            if assignment.consumer_id not in known_consumers:
                issues.append(ModelRoutingDiagnosticIssue(
                    severity="error", reason_code="consumer_unresolved", reference=key,
                ))
            elif assignment.mode == "profile" and assignment.profile_id not in known_profiles:
                issues.append(ModelRoutingDiagnosticIssue(
                    severity="error", reason_code="profile_unresolved", reference=key,
                ))
            elif assignment.mode == "model" and (
                assignment.provider_id, assignment.model_id
            ) not in known_models:
                issues.append(ModelRoutingDiagnosticIssue(
                    severity="warning", reason_code="model_unresolved", reference=key,
                ))
        for group in configuration.fallback_groups:
            for candidate in group.candidates:
                if candidate.profile_id not in known_profiles:
                    issues.append(ModelRoutingDiagnosticIssue(
                        severity="error",
                        reason_code="fallback_profile_unresolved",
                        reference=f"{group.group_id}:{candidate.profile_id}",
                    ))
        for source in catalog.sources:
            if source.status in {"degraded", "unavailable", "stale"}:
                issues.append(ModelRoutingDiagnosticIssue(
                    severity="warning",
                    reason_code=f"inventory_source_{source.status}",
                    reference=source.source_id,
                ))
        for route in routes:
            if not route.executable:
                issues.append(ModelRoutingDiagnosticIssue(
                    severity="warning",
                    reason_code="effective_route_not_executable",
                    reference=route.consumer_id,
                ))
        unresolved = sum(
            item.reason_code in {
                "consumer_unresolved", "profile_unresolved", "model_unresolved",
                "fallback_profile_unresolved",
            }
            for item in issues
        )
        return ModelRoutingDiagnostics(
            generated_at=_now(),
            configuration_revision=configuration.revision,
            catalog_revision=catalog.catalog_revision,
            assignment_count=len(configuration.assignments),
            fallback_group_count=len(configuration.fallback_groups),
            routable_consumer_count=sum(item.routable for item in consumer_values),
            unresolved_assignment_count=unresolved,
            non_executable_route_count=sum(not item.executable for item in routes),
            source_statuses=catalog.sources,
            issues=tuple(issues),
            usage=tuple(usage),
        )


_USAGE = ModelRoutingUsageProjection()


def get_model_routing_usage_projection() -> ModelRoutingUsageProjection:
    return _USAGE


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "ModelRoutingDiagnosticsService", "ModelRoutingUsageProjection",
    "get_model_routing_usage_projection",
]
