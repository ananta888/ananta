"""Pure validation shared by custom-composition admission and compilation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agent.models.organization_models import (
    OrganizationBlueprintDefinition,
    VersionedDefinitionRef,
    canonical_sha256,
)


class OrganizationCustomCompositionError(ValueError):
    def __init__(
        self,
        reason_code: str,
        *,
        path: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.path = path
        self.details = dict(details or {})
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class OrganizationCustomComposition:
    team_blueprint_counts: dict[str, int]
    singleton_unit_keys: tuple[str, ...]
    group_counts: dict[str, int]
    team_count: int
    capability_gaps: tuple[str, ...]


def custom_composition_digest(
    *,
    definition_ref: str,
    definition_revision: str,
    policy_hash: str,
    composition: Mapping[str, int],
) -> str:
    """Bind an exception to exact immutable definition, policy and counts."""

    return canonical_sha256(
        {
            "schema": "organization_custom_composition_binding.v1",
            "definition_ref": str(definition_ref),
            "definition_revision": str(definition_revision),
            "policy_hash": str(policy_hash),
            "team_blueprint_counts": {str(key): int(value) for key, value in sorted(composition.items())},
        }
    )


class OrganizationCustomCompositionService:
    """Validate custom N without persistence, HTTP or admission side effects."""

    def validate(
        self,
        *,
        definition: OrganizationBlueprintDefinition,
        composition: Mapping[str, int],
        maximum_team_count: int,
    ) -> OrganizationCustomComposition:
        if not composition:
            self._error(
                "ORGANIZATION_CUSTOM_COMPOSITION_REQUIRED",
                path="$.custom_composition",
            )
        singleton_by_blueprint = {
            VersionedDefinitionRef.parse(str(unit.team_blueprint_ref)).key: unit
            for unit in definition.units
            if unit.materialization_kind == "team_instance" and unit.team_blueprint_ref
        }
        groups_by_blueprint = {
            VersionedDefinitionRef.parse(group.team_blueprint_ref).key: group for group in definition.unit_groups
        }
        normalized: dict[str, int] = {}
        singleton_keys: list[str] = []
        group_counts: dict[str, int] = {}
        total = 0
        for raw_key, raw_count in sorted(composition.items()):
            key = str(raw_key or "").strip()
            if not key or isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 1:
                self._error(
                    "ORGANIZATION_CUSTOM_COUNT_INVALID",
                    path=f"$.custom_composition.{key or '<empty>'}",
                )
            if key in normalized:
                self._error(
                    "ORGANIZATION_CUSTOM_BLUEPRINT_DUPLICATE",
                    path=f"$.custom_composition.{key}",
                )
            singleton = singleton_by_blueprint.get(key)
            group = groups_by_blueprint.get(key)
            if singleton is not None:
                if raw_count != 1:
                    self._error(
                        "ORGANIZATION_SINGLETON_COUNT_INVALID",
                        path=f"$.custom_composition.{key}",
                    )
                singleton_keys.append(singleton.unit_key)
            elif group is not None:
                if raw_count < group.min_count or (group.max_count is not None and raw_count > group.max_count):
                    self._error(
                        "ORGANIZATION_GROUP_COUNT_INVALID",
                        path=f"$.custom_composition.{key}",
                        details={
                            "minimum": group.min_count,
                            "maximum": group.max_count,
                        },
                    )
                group_counts[group.group_id] = raw_count
            else:
                self._error(
                    "ORGANIZATION_CUSTOM_BLUEPRINT_UNKNOWN",
                    path=f"$.custom_composition.{key}",
                )
            normalized[key] = raw_count
            total += raw_count

        if total < 2:
            self._error(
                "ORGANIZATION_TEAM_COUNT_BELOW_MINIMUM",
                path="$.custom_composition",
                details={"requested": total, "minimum": 2},
            )
        if total > int(maximum_team_count):
            self._error(
                "ORGANIZATION_TEAM_LIMIT_EXCEEDED",
                path="$.custom_composition",
                details={"requested": total, "limit": int(maximum_team_count)},
            )

        missing = set(definition.standard_composition.baseline_singleton_team_refs) - set(singleton_keys)
        code_by_key = {
            "portfolio_product_coordination": "STANDARD_CAPABILITY_GAP_PORTFOLIO",
            "research_and_discovery": "STANDARD_CAPABILITY_GAP_RESEARCH",
            "platform_devops_sre": "STANDARD_CAPABILITY_GAP_PLATFORM",
            "quality_security_release": "STANDARD_CAPABILITY_GAP_QUALITY_RELEASE",
            "architecture_governance": "STANDARD_CAPABILITY_GAP_ARCHITECTURE",
            "proof_of_concept": "STANDARD_CAPABILITY_GAP_POC",
        }
        gaps = [code_by_key.get(key, f"STANDARD_CAPABILITY_GAP_{key.upper()}") for key in sorted(missing)]
        required_delivery = definition.standard_composition.baseline_group_counts.get(
            "product_delivery",
            0,
        )
        if group_counts.get("product_delivery", 0) < required_delivery:
            gaps.append("STANDARD_CAPABILITY_GAP_DELIVERY_REDUNDANCY")
        return OrganizationCustomComposition(
            team_blueprint_counts=normalized,
            singleton_unit_keys=tuple(singleton_keys),
            group_counts=group_counts,
            team_count=total,
            capability_gaps=tuple(gaps),
        )

    @staticmethod
    def _error(
        reason_code: str,
        *,
        path: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        raise OrganizationCustomCompositionError(
            reason_code,
            path=path,
            details=details,
        )


__all__ = [
    "OrganizationCustomComposition",
    "OrganizationCustomCompositionError",
    "OrganizationCustomCompositionService",
    "custom_composition_digest",
]
