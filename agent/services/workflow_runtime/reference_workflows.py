"""Deterministic reference workflows shared by runtime conformance gates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.services.workflow_runtime.execution_plan import ExecutionPlan

REFERENCE_WORKFLOW_CATALOG_SCHEMA = "ananta.reference_workflow_catalog.v1"
REFERENCE_WORKFLOW_CATALOG_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "workflow_runtime" / "reference_workflows.v1.json"
)
SUPPORT_LEVELS = frozenset({"target", "incompatible"})
REFERENCE_VARIANT_KINDS = frozenset({"policy_denial", "runtime_failure"})


@dataclass(frozen=True)
class ReferenceScenarioVariant:
    """Deterministic negative case bound to one reference plan.

    Variants describe observable contracts only.  They deliberately contain no
    framework exception types or executable callbacks, so every runtime adapter
    receives the same failure/policy oracle.
    """

    variant_id: str
    kind: str
    reason_code: str
    terminal_status: str
    required_event_types: tuple[str, ...]
    side_effect_operations: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "ReferenceScenarioVariant":
        value = cls(
            variant_id=str(raw.get("variant_id") or "").strip(),
            kind=str(raw.get("kind") or "").strip(),
            reason_code=str(raw.get("reason_code") or "").strip(),
            terminal_status=str(raw.get("terminal_status") or "").strip(),
            required_event_types=tuple(str(item) for item in raw.get("required_event_types", ())),
            side_effect_operations=tuple(
                str(item) for item in raw.get("side_effect_operations", ())
            ),
        )
        value.validate()
        return value

    def validate(self) -> None:
        if not self.variant_id or not self.reason_code or not self.terminal_status:
            raise ValueError("reference_workflow_variant_binding_invalid")
        if self.kind not in REFERENCE_VARIANT_KINDS:
            raise ValueError(f"reference_workflow_variant_kind_invalid:{self.variant_id}")
        if not self.required_event_types:
            raise ValueError(f"reference_workflow_variant_events_missing:{self.variant_id}")


@dataclass(frozen=True)
class ReferenceInvariants:
    terminal_statuses: tuple[str, ...]
    required_event_types: tuple[str, ...]
    required_artifacts: tuple[str, ...]
    required_gates: tuple[str, ...]
    side_effect_operations: tuple[str, ...]
    required_policy_decisions: tuple[str, ...]
    budget_limits: dict[str, int | float]

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "ReferenceInvariants":
        def values(key: str) -> tuple[str, ...]:
            return tuple(str(value) for value in raw.get(key, ()))

        return cls(
            terminal_statuses=values("terminal_statuses"),
            required_event_types=values("required_event_types"),
            required_artifacts=values("required_artifacts"),
            required_gates=values("required_gates"),
            side_effect_operations=values("side_effect_operations"),
            required_policy_decisions=values("required_policy_decisions"),
            budget_limits={
                str(key): value
                for key, value in dict(raw.get("budget_limits") or {}).items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            },
        )


@dataclass(frozen=True)
class ReferenceWorkflow:
    scenario_id: str
    description: str
    plan: ExecutionPlan
    invariants: ReferenceInvariants
    support: dict[str, str]
    variants: tuple[ReferenceScenarioVariant, ...] = ()

    def support_for(self, runtime_id: str) -> str:
        return self.support.get(runtime_id, "incompatible")


def load_reference_workflows(
    path: str | Path = REFERENCE_WORKFLOW_CATALOG_PATH,
) -> tuple[ReferenceWorkflow, ...]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("schema") != REFERENCE_WORKFLOW_CATALOG_SCHEMA:
        raise ValueError("reference_workflow_catalog_schema_unsupported")
    if raw.get("catalog_version") != "1.0.0":
        raise ValueError("reference_workflow_catalog_version_unsupported")

    scenarios: list[ReferenceWorkflow] = []
    seen: set[str] = set()
    for item in raw.get("scenarios", ()):  # fail closed below for empty/malformed catalogs
        scenario_id = str(item.get("scenario_id") or "").strip()
        if not scenario_id or scenario_id in seen:
            raise ValueError("reference_workflow_scenario_id_invalid")
        seen.add(scenario_id)
        support = {str(key): str(value) for key, value in dict(item.get("support") or {}).items()}
        if set(support.values()) - SUPPORT_LEVELS:
            raise ValueError(f"reference_workflow_support_invalid:{scenario_id}")
        variants = tuple(
            ReferenceScenarioVariant.from_mapping(dict(value))
            for value in item.get("variants", ())
            if isinstance(value, dict)
        )
        kinds = {variant.kind for variant in variants}
        if kinds != REFERENCE_VARIANT_KINDS:
            raise ValueError(f"reference_workflow_negative_variants_incomplete:{scenario_id}")
        if len({variant.variant_id for variant in variants}) != len(variants):
            raise ValueError(f"reference_workflow_variant_id_duplicate:{scenario_id}")
        scenarios.append(
            ReferenceWorkflow(
                scenario_id=scenario_id,
                description=str(item.get("description") or "").strip(),
                plan=ExecutionPlan.from_mapping(dict(item.get("plan") or {})),
                invariants=ReferenceInvariants.from_mapping(dict(item.get("invariants") or {})),
                support=support,
                variants=variants,
            )
        )
    if len(scenarios) != 5:
        raise ValueError(f"reference_workflow_catalog_incomplete:{len(scenarios)}")
    return tuple(scenarios)
