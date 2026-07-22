"""Conservative, machine-readable admission limits for SFU baseline runs."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


REQUIRED_PROFILE_IDS = frozenset(
    {
        "direct_pair_regression",
        "sfu_direct_network",
        "sfu_all_turn",
        "webinar",
        "multi_publisher",
        "screen_share",
        "transcript_fanout",
        "semantic_fanout",
    }
)
REQUIRED_BUDGET_IDS = frozenset(
    {
        "join_latency_ms",
        "rekey_latency_ms",
        "layer_switch_latency_ms",
        "node_failover_recovery_ms",
        "queue_lag_ms",
        "turn_egress_bytes_per_minute",
        "cleanup_latency_ms",
        "memory_growth_bytes_per_minute",
        "fd_growth_count_per_hour",
        "database_row_growth_count_per_room",
        "capacity_reserve_percent",
        "retry_rate_percent",
    }
)


class BaselineLimitError(ValueError):
    """Raised for ambiguous or unsafe limit catalogs."""


@dataclass(frozen=True)
class NumericBudget:
    identifier: str
    unit: str
    window: str
    percentile: str
    minimum: Decimal
    maximum: Decimal
    missing_metric_behavior: str


@dataclass(frozen=True)
class RunProfile:
    identifier: str
    warmup_seconds: int
    measurement_seconds: int
    repetitions_min: int
    confidence_level: Decimal
    variance_max_percent: Decimal
    capacity_reserve_percent: Decimal
    retry_rate_max_percent: Decimal
    budget_set_id: str


@dataclass(frozen=True)
class BaselineLimitCatalog:
    schema_version: str
    policy_version: int
    activation_default: str
    hard_limits: Mapping[str, NumericBudget]
    budgets: Mapping[str, NumericBudget]
    budget_sets: Mapping[str, tuple[str, ...]]
    run_profiles: Mapping[str, RunProfile]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "BaselineLimitCatalog":
        schema_version = _non_empty_string(raw.get("schema_version"), "schema_version")
        policy_version = _positive_integer(raw.get("policy_version"), "policy_version")
        activation_default = _non_empty_string(raw.get("activation_default"), "activation_default")
        if activation_default not in {"go", "no_go"}:
            raise BaselineLimitError("activation_default_invalid")

        hard_limits = _parse_budget_mapping(raw.get("hard_limits"), require_window=False)
        budgets = _parse_budget_mapping(raw.get("budget_definitions"), require_window=True)
        if REQUIRED_BUDGET_IDS - set(budgets):
            raise BaselineLimitError("required_budget_missing")

        raw_sets = _mapping(raw.get("budget_sets"), "budget_sets")
        budget_sets: dict[str, tuple[str, ...]] = {}
        for set_id, raw_ids in raw_sets.items():
            if not isinstance(set_id, str) or not set_id:
                raise BaselineLimitError("budget_set_id_invalid")
            if not isinstance(raw_ids, list) or not raw_ids:
                raise BaselineLimitError("budget_set_empty")
            identifiers = tuple(raw_ids)
            if any(not isinstance(item, str) or item not in budgets for item in identifiers):
                raise BaselineLimitError("budget_set_reference_invalid")
            if len(set(identifiers)) != len(identifiers):
                raise BaselineLimitError("budget_set_duplicate")
            if not REQUIRED_BUDGET_IDS.issubset(identifiers):
                raise BaselineLimitError("activation_budget_set_incomplete")
            budget_sets[set_id] = identifiers

        raw_profiles = raw.get("run_profiles")
        if not isinstance(raw_profiles, list):
            raise BaselineLimitError("run_profiles_invalid")
        profiles: dict[str, RunProfile] = {}
        for value in raw_profiles:
            item = _mapping(value, "run_profile")
            identifier = _non_empty_string(item.get("id"), "run_profile_id")
            if identifier in profiles:
                raise BaselineLimitError("run_profile_duplicate")
            budget_set_id = _non_empty_string(item.get("budget_set_id"), "budget_set_id")
            if budget_set_id not in budget_sets:
                raise BaselineLimitError("run_profile_budget_set_unknown")
            profiles[identifier] = RunProfile(
                identifier=identifier,
                warmup_seconds=_positive_integer(item.get("warmup_seconds"), "warmup_seconds"),
                measurement_seconds=_positive_integer(
                    item.get("measurement_seconds"), "measurement_seconds"
                ),
                repetitions_min=_positive_integer(item.get("repetitions_min"), "repetitions_min"),
                confidence_level=_bounded_decimal(
                    item.get("confidence_level"), "confidence_level", 0, 1
                ),
                variance_max_percent=_bounded_decimal(
                    item.get("variance_max_percent"), "variance_max_percent", 0, 100
                ),
                capacity_reserve_percent=_bounded_decimal(
                    item.get("capacity_reserve_percent"), "capacity_reserve_percent", 0, 100
                ),
                retry_rate_max_percent=_bounded_decimal(
                    item.get("retry_rate_max_percent"), "retry_rate_max_percent", 0, 100
                ),
                budget_set_id=budget_set_id,
            )
        if not REQUIRED_PROFILE_IDS.issubset(profiles):
            raise BaselineLimitError("required_run_profile_missing")

        return cls(
            schema_version=schema_version,
            policy_version=policy_version,
            activation_default=activation_default,
            hard_limits=MappingProxyType(hard_limits),
            budgets=MappingProxyType(budgets),
            budget_sets=MappingProxyType(budget_sets),
            run_profiles=MappingProxyType(profiles),
        )


@dataclass(frozen=True)
class BaselineQualification:
    status: str
    reason_codes: tuple[str, ...]
    profile_id: str
    policy_version: int


def load_baseline_limit_catalog(path: str | Path) -> BaselineLimitCatalog:
    with Path(path).open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise BaselineLimitError("catalog_root_invalid")
    return BaselineLimitCatalog.from_mapping(raw)


def qualify_baseline_run(
    catalog: BaselineLimitCatalog,
    report: Mapping[str, object],
    *,
    grounding_verified: bool,
) -> BaselineQualification:
    reasons: set[str] = set()
    profile_id = report.get("profile_id")
    if not isinstance(profile_id, str) or profile_id not in catalog.run_profiles:
        return BaselineQualification(
            "no_go", ("run_profile_unknown",), "unknown", catalog.policy_version
        )
    profile = catalog.run_profiles[profile_id]

    _compare_minimum(report, "repetitions", Decimal(profile.repetitions_min), reasons)
    _compare_minimum(report, "confidence_level", profile.confidence_level, reasons)
    _compare_maximum(report, "variance_percent", profile.variance_max_percent, reasons)
    _compare_minimum(
        report, "capacity_reserve_percent", profile.capacity_reserve_percent, reasons
    )
    _compare_maximum(report, "retry_rate_percent", profile.retry_rate_max_percent, reasons)

    raw_metrics = report.get("metrics")
    if not isinstance(raw_metrics, Mapping):
        reasons.add("metrics_missing")
        raw_metrics = {}
    for budget_id in catalog.budget_sets[profile.budget_set_id]:
        budget = catalog.budgets[budget_id]
        if budget_id not in raw_metrics:
            reasons.add("required_metric_missing")
            continue
        try:
            measured = _finite_decimal(raw_metrics[budget_id], budget_id)
        except BaselineLimitError:
            reasons.add("metric_value_invalid")
            continue
        if measured < budget.minimum or measured > budget.maximum:
            reasons.add("metric_outside_budget")

    if not grounding_verified:
        reasons.add("evidence_not_grounded")
    if catalog.activation_default != "go":
        reasons.add("catalog_activation_default_no_go")
    return BaselineQualification(
        status="qualified" if not reasons else "no_go",
        reason_codes=tuple(sorted(reasons)),
        profile_id=profile_id,
        policy_version=catalog.policy_version,
    )


def _parse_budget_mapping(value: object, *, require_window: bool) -> dict[str, NumericBudget]:
    raw = _mapping(value, "budget_mapping")
    parsed: dict[str, NumericBudget] = {}
    for identifier, raw_budget in raw.items():
        if not isinstance(identifier, str) or not identifier:
            raise BaselineLimitError("budget_id_invalid")
        budget = _mapping(raw_budget, "budget")
        unit = _non_empty_string(budget.get("unit"), "budget_unit")
        if unit.lower() in {"unitless", "none", "n/a"}:
            raise BaselineLimitError("budget_unit_invalid")
        window = _non_empty_string(budget.get("window", "static"), "budget_window")
        percentile = _non_empty_string(
            budget.get("percentile", "max"), "budget_percentile"
        )
        if require_window and ("window" not in budget or "percentile" not in budget):
            raise BaselineLimitError("budget_sampling_definition_missing")
        minimum = _finite_decimal(budget.get("minimum"), "budget_minimum")
        maximum = _finite_decimal(budget.get("maximum"), "budget_maximum")
        if minimum < 0 or maximum < minimum:
            raise BaselineLimitError("budget_range_invalid")
        missing = _non_empty_string(
            budget.get("missing_metric_behavior"), "missing_metric_behavior"
        )
        if missing != "block":
            raise BaselineLimitError("missing_metric_behavior_must_block")
        parsed[identifier] = NumericBudget(
            identifier,
            unit,
            window,
            percentile,
            minimum,
            maximum,
            missing,
        )
    if not parsed:
        raise BaselineLimitError("budget_mapping_empty")
    return parsed


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BaselineLimitError(f"{field}_invalid")
    return value


def _non_empty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BaselineLimitError(f"{field}_invalid")
    return value.strip()


def _positive_integer(value: object, field: str) -> int:
    decimal = _finite_decimal(value, field)
    if decimal <= 0 or decimal != decimal.to_integral_value():
        raise BaselineLimitError(f"{field}_invalid")
    return int(decimal)


def _bounded_decimal(value: object, field: str, minimum: int, maximum: int) -> Decimal:
    decimal = _finite_decimal(value, field)
    if decimal < minimum or decimal > maximum:
        raise BaselineLimitError(f"{field}_invalid")
    return decimal


def _finite_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise BaselineLimitError(f"{field}_invalid")
    if isinstance(value, float) and not math.isfinite(value):
        raise BaselineLimitError(f"{field}_invalid")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise BaselineLimitError(f"{field}_invalid") from None
    if not decimal.is_finite():
        raise BaselineLimitError(f"{field}_invalid")
    return decimal


def _compare_minimum(
    report: Mapping[str, object], field: str, minimum: Decimal, reasons: set[str]
) -> None:
    try:
        value = _finite_decimal(report.get(field), field)
    except BaselineLimitError:
        reasons.add(f"{field}_invalid")
        return
    if value < minimum:
        reasons.add(f"{field}_below_minimum")


def _compare_maximum(
    report: Mapping[str, object], field: str, maximum: Decimal, reasons: set[str]
) -> None:
    try:
        value = _finite_decimal(report.get(field), field)
    except BaselineLimitError:
        reasons.add(f"{field}_invalid")
        return
    if value > maximum:
        reasons.add(f"{field}_above_maximum")


__all__ = [
    "BaselineLimitCatalog",
    "BaselineLimitError",
    "BaselineQualification",
    "NumericBudget",
    "REQUIRED_BUDGET_IDS",
    "REQUIRED_PROFILE_IDS",
    "RunProfile",
    "load_baseline_limit_catalog",
    "qualify_baseline_run",
]
