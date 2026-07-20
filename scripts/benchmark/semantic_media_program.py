#!/usr/bin/env python3
"""Validate comparable program benchmark evidence; never fabricates samples."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from itertools import product
from pathlib import Path
from typing import Any, Mapping

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.services.semantic_media_program_evidence import (  # noqa: E402
    GateEvidence,
    ProgramEvidenceError,
    canonical_sha256,
    source_hash,
    unavailable_evidence,
    write_report,
)
from scripts.benchmark.semantic_media_program_executor import (  # noqa: E402
    METRIC_FIELDS as EXECUTOR_METRIC_FIELDS,
)
from scripts.benchmark.semantic_media_program_executor import (  # noqa: E402
    PRODUCT_SOURCE_PATHS,
    ProgramBenchmarkExecutionError,
    current_policy,
    current_quality_bindings,
    current_source_sha256,
    recompute_comparison_binding,
    run_benchmark,
)

ROOT = _PROJECT_ROOT
WINDOWS = frozenset({2, 10, 20})
OFFLINE_FACTORS = frozenset({1, 2, 5, 10, 20})
TOPOLOGIES = frozenset({"pair", "group", "evidence", "offline"})
METRIC_FIELDS = frozenset(
    {
        "ingress_bytes",
        "egress_bytes",
        "turn_bytes",
        "cpu_micros",
        "gpu_micros",
        "ram_bytes",
        "vram_bytes",
        "disk_bytes",
        "energy_microwh",
        "latency_p50_ms",
        "latency_p95_ms",
        "latency_p99_ms",
        "worst_burst_bytes",
        "recovery_ms",
        "open_resources",
    }
)
THRESHOLDS = {
    "maximum_live_p95_ratio_micros": 1_050_000,
    "maximum_live_p99_ratio_micros": 1_050_000,
    "maximum_resource_growth_per_receiver_bytes": 16 * 1024 * 1024,
}
EXECUTION_POLICY = current_policy()
V2_THRESHOLDS = dict(EXECUTION_POLICY["thresholds"])
V2_RUN_CONFIG_FIELDS = frozenset(
    {
        "duration_seconds",
        "width",
        "height",
        "framerate",
        "audio_format",
        "network_profiles",
        "hardware_sha256",
        "model_sha256",
        "policy_sha256",
        "source_sha256",
        "fixture_sha256",
        "seed",
        "timeout_seconds",
        "execution_mode",
        "quality_bindings",
    }
)
V2_MODE_FIELDS = frozenset(
    {
        "binding_sha256",
        "values",
        "availability",
        "latency_sample_count",
        "expected_deliveries",
        "completed_deliveries",
        "valid_deliveries",
        "offline_saturation",
    }
)


def evaluate(report: Mapping[str, Any]) -> tuple[GateEvidence, dict[str, int | bool | str]]:
    if report.get("schema") == "ananta.semantic-media-program-benchmark.v2":
        return _evaluate_v2(report)
    return _evaluate_v1(report)


def _evaluate_v1(report: Mapping[str, Any]) -> tuple[GateEvidence, dict[str, int | bool]]:
    reasons: list[str] = []
    if (
        set(report) != {"schema", "run_config", "rows"}
        or report.get("schema") != "ananta.semantic-media-program-benchmark.v1"
    ):
        raise ProgramEvidenceError("program_benchmark_contract_invalid")
    config = report.get("run_config")
    if not isinstance(config, Mapping) or set(config) != {
        "duration_seconds",
        "width",
        "height",
        "framerate",
        "audio_format",
        "network_profiles",
        "hardware_sha256",
        "model_sha256",
        "policy_sha256",
        "source_sha256",
    }:
        raise ProgramEvidenceError("program_benchmark_config_invalid")
    for name in ("hardware_sha256", "model_sha256", "policy_sha256", "source_sha256"):
        _digest(config[name])
    if not isinstance(config["network_profiles"], list) or not config["network_profiles"]:
        reasons.append("program_benchmark_network_profiles_missing")
    rows = report.get("rows")
    if not isinstance(rows, list) or not rows:
        reasons.append("program_benchmark_rows_missing")
        rows = []
    seen_windows: set[int] = set()
    seen_topologies: set[str] = set()
    seen_offline: set[int] = set()
    seen_receivers: set[int] = set()
    seen_live_matrix: set[tuple[str, int, int]] = set()
    seen_offline_matrix: set[int] = set()
    maximum_p95_ratio = 0
    maximum_p99_ratio = 0
    maximum_growth = 0
    quality_failures = 0
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "topology",
            "window_seconds",
            "receivers",
            "offline_factor",
            "network_profile",
            "quality_passed",
            "quality_digest",
            "claimed_savings",
            "ordinary",
            "semantic",
        }:
            reasons.append("program_benchmark_row_shape_invalid")
            continue
        topology = str(row["topology"])
        window = _positive_integer(row["window_seconds"])
        receivers = _positive_integer(row["receivers"])
        factor = _positive_integer(row["offline_factor"])
        if topology not in TOPOLOGIES:
            reasons.append("program_benchmark_topology_invalid")
            continue
        if window not in WINDOWS:
            reasons.append("program_benchmark_window_invalid")
        if str(row["network_profile"]) not in config["network_profiles"]:
            reasons.append("program_benchmark_network_profile_invalid")
        if type(row["quality_passed"]) is not bool or type(row["claimed_savings"]) is not bool:
            reasons.append("program_benchmark_boolean_invalid")
            continue
        seen_topologies.add(topology)
        seen_windows.add(window)
        seen_receivers.add(receivers)
        if topology == "offline":
            seen_offline.add(factor)
            if factor in seen_offline_matrix:
                reasons.append("program_benchmark_duplicate_measurement")
            seen_offline_matrix.add(factor)
            if window != 20 or receivers != 2:
                reasons.append("program_benchmark_offline_matrix_invalid")
        else:
            key = (topology, window, receivers)
            if key in seen_live_matrix:
                reasons.append("program_benchmark_duplicate_measurement")
            seen_live_matrix.add(key)
            if factor != 1:
                reasons.append("program_benchmark_live_factor_invalid")
        _digest(row["quality_digest"])
        if row["claimed_savings"] is True and row["quality_passed"] is not True:
            reasons.append("program_benchmark_unqualified_savings")
        if row["quality_passed"] is not True:
            quality_failures += 1
        ordinary = _metrics(row["ordinary"])
        semantic = _metrics(row["semantic"])
        if not _ordered_percentiles(ordinary) or not _ordered_percentiles(semantic):
            reasons.append("program_benchmark_percentile_order_invalid")
        p95_ratio = _ratio_micros(semantic["latency_p95_ms"], ordinary["latency_p95_ms"])
        p99_ratio = _ratio_micros(semantic["latency_p99_ms"], ordinary["latency_p99_ms"])
        maximum_p95_ratio = max(maximum_p95_ratio, p95_ratio)
        maximum_p99_ratio = max(maximum_p99_ratio, p99_ratio)
        if receivers > 0:
            growth = max(0, semantic["ram_bytes"] - ordinary["ram_bytes"]) // receivers
            maximum_growth = max(maximum_growth, growth)
    if not WINDOWS <= seen_windows:
        reasons.append("program_benchmark_window_coverage_missing")
    if not TOPOLOGIES <= seen_topologies:
        reasons.append("program_benchmark_topology_coverage_missing")
    if not OFFLINE_FACTORS <= seen_offline:
        reasons.append("program_benchmark_offline_scale_missing")
    if not {2, 10, 100} <= seen_receivers:
        reasons.append("program_benchmark_receiver_scale_missing")
    required_live_matrix = set(product(("pair", "group", "evidence"), WINDOWS, (2, 10, 100)))
    if seen_live_matrix != required_live_matrix:
        reasons.append("program_benchmark_live_matrix_incomplete")
    if seen_offline_matrix != set(OFFLINE_FACTORS):
        reasons.append("program_benchmark_offline_matrix_incomplete")
    if maximum_p95_ratio > THRESHOLDS["maximum_live_p95_ratio_micros"]:
        reasons.append("program_benchmark_live_p95_regression")
    if maximum_p99_ratio > THRESHOLDS["maximum_live_p99_ratio_micros"]:
        reasons.append("program_benchmark_live_p99_regression")
    if maximum_growth > THRESHOLDS["maximum_resource_growth_per_receiver_bytes"]:
        reasons.append("program_benchmark_resource_growth_unbounded")
    source_digest = source_hash(
        ROOT,
        (
            "agent/services/semantic_media_program_evidence.py",
            "scripts/benchmark/semantic_media_program.py",
            "docs/benchmarks/semantic-media-program-methodology.md",
        ),
    )
    config_digest = canonical_sha256({"run_config": config, "thresholds": THRESHOLDS})
    measurements: dict[str, int | bool] = {
        "row_count": len(rows),
        "quality_failure_count": quality_failures,
        "maximum_p95_ratio_micros": maximum_p95_ratio,
        "maximum_p99_ratio_micros": maximum_p99_ratio,
        "maximum_growth_per_receiver_bytes": maximum_growth,
    }
    status = "failed" if reasons else "unverified"
    reasons.append("program_benchmark_legacy_not_release_eligible")
    evidence = GateEvidence(
        "ASMP-QA-009",
        status,
        tuple(sorted(set(reasons))),
        source_digest,
        config_digest,
        measurements,
    )
    return evidence, measurements


def _evaluate_v2(  # noqa: C901 - one closed-contract policy reducer keeps row invariants co-located.
    report: Mapping[str, Any],
) -> tuple[GateEvidence, dict[str, int | bool | str]]:
    reasons: list[str] = []
    if set(report) != {"schema", "run_config", "measurement_contract", "rows"}:
        raise ProgramEvidenceError("program_benchmark_contract_invalid")
    _assert_measurement_report_content_free(report)
    config = report.get("run_config")
    if not isinstance(config, Mapping) or set(config) != V2_RUN_CONFIG_FIELDS:
        raise ProgramEvidenceError("program_benchmark_config_invalid")
    _validate_v2_config_values(config)
    for name in (
        "hardware_sha256",
        "model_sha256",
        "policy_sha256",
        "source_sha256",
        "fixture_sha256",
    ):
        _digest(config[name])
    if config["source_sha256"] != current_source_sha256():
        reasons.append("program_benchmark_source_binding_stale")
    if config["policy_sha256"] != _policy_sha256():
        reasons.append("program_benchmark_policy_binding_stale")
    expected_fixture = canonical_sha256(EXECUTION_POLICY["source_fixture"])
    if config["fixture_sha256"] != expected_fixture:
        reasons.append("program_benchmark_fixture_binding_stale")
    if config["seed"] != EXECUTION_POLICY["seed"]:
        reasons.append("program_benchmark_seed_invalid")
    if config["execution_mode"] != "measured-product-contract-loopback":
        reasons.append("program_benchmark_execution_mode_invalid")
    if (
        config["duration_seconds"] != EXECUTION_POLICY["source_fixture"]["duration_seconds"]
        or config["width"] != EXECUTION_POLICY["source_fixture"]["width"]
        or config["height"] != EXECUTION_POLICY["source_fixture"]["height"]
        or config["framerate"] != EXECUTION_POLICY["source_fixture"]["framerate"]
        or config["audio_format"] != EXECUTION_POLICY["source_fixture"]["audio_format"]
    ):
        reasons.append("program_benchmark_fixture_config_invalid")
    if config["network_profiles"] != EXECUTION_POLICY["matrix"]["network_profiles"]:
        reasons.append("program_benchmark_network_profiles_missing")
    if config.get("quality_bindings") != current_quality_bindings():
        reasons.append("program_benchmark_quality_evidence_stale")
    measurement_contract = report.get("measurement_contract")
    if not isinstance(measurement_contract, Mapping) or set(measurement_contract) != {
        "clock",
        "live_transport",
        "security",
        "offline_runtime",
        "metric_fields",
        "policy_sha256",
    }:
        raise ProgramEvidenceError("program_benchmark_measurement_contract_invalid")
    if (
        measurement_contract.get("clock") != "perf_counter_ns"
        or measurement_contract.get("live_transport") != "udp-ipv4-loopback"
        or measurement_contract.get("security") != "production-aes-gcm-envelope"
        or measurement_contract.get("offline_runtime") != "production-speech-reconciliation-resolver"
        or measurement_contract.get("metric_fields") != list(EXECUTOR_METRIC_FIELDS)
        or measurement_contract.get("policy_sha256") != config["policy_sha256"]
    ):
        reasons.append("program_benchmark_measurement_contract_invalid")

    rows = report.get("rows")
    if not isinstance(rows, list) or not rows:
        reasons.append("program_benchmark_rows_missing")
        rows = []
    seen_live_matrix: set[tuple[str, int, int]] = set()
    seen_offline_matrix: set[int] = set()
    maximum_p95_ratio = 0
    maximum_p99_ratio = 0
    maximum_growth = 0
    quality_failures = 0
    unavailable_metrics: set[str] = set()
    maximum_sound_p95 = 0
    maximum_sound_p99 = 0
    maximum_text_p95 = 0
    maximum_text_p99 = 0
    maximum_ui_p95 = 0
    maximum_ui_p99 = 0
    saturated_factor_count = 0
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "topology",
            "window_seconds",
            "receivers",
            "offline_factor",
            "network_profile",
            "comparison_binding_sha256",
            "quality",
            "claimed_savings",
            "ordinary",
            "semantic",
        }:
            reasons.append("program_benchmark_row_shape_invalid")
            continue
        topology = str(row["topology"])
        window = _positive_integer(row["window_seconds"])
        receivers = _positive_integer(row["receivers"])
        factor = _positive_integer(row["offline_factor"])
        if topology not in TOPOLOGIES:
            reasons.append("program_benchmark_topology_invalid")
            continue
        if window not in WINDOWS:
            reasons.append("program_benchmark_window_invalid")
        expected_network = "offline" if topology == "offline" else config["network_profiles"][0]
        if row["network_profile"] != expected_network:
            reasons.append("program_benchmark_network_profile_invalid")
        _digest(row["comparison_binding_sha256"])
        if row["comparison_binding_sha256"] != recompute_comparison_binding(config, row):
            reasons.append("program_benchmark_comparison_binding_mismatch")
        if type(row["claimed_savings"]) is not bool:
            reasons.append("program_benchmark_boolean_invalid")
            continue
        ordinary, ordinary_availability = _metrics_v2(
            row["ordinary"],
            expected_binding=str(row["comparison_binding_sha256"]),
            topology=topology,
            reasons=reasons,
        )
        semantic, semantic_availability = _metrics_v2(
            row["semantic"],
            expected_binding=str(row["comparison_binding_sha256"]),
            topology=topology,
            reasons=reasons,
        )
        _validate_metric_availability(
            topology=topology,
            ordinary=ordinary_availability,
            semantic=semantic_availability,
            unavailable_metrics=unavailable_metrics,
            reasons=reasons,
        )
        quality_passed = _quality_v2(row["quality"], reasons)
        if not quality_passed:
            quality_failures += 1
            reasons.append("program_benchmark_quality_gate_failed")
        if row["claimed_savings"] is True and not quality_passed:
            reasons.append("program_benchmark_unqualified_savings")
        if topology == "offline":
            if factor in seen_offline_matrix:
                reasons.append("program_benchmark_duplicate_measurement")
            seen_offline_matrix.add(factor)
            if window != 20 or receivers != 2:
                reasons.append("program_benchmark_offline_matrix_invalid")
            ordinary_saturation = row["ordinary"].get("offline_saturation")
            semantic_saturation = row["semantic"].get("offline_saturation")
            saturation_metrics = _validate_offline_saturation(
                ordinary_saturation,
                semantic_saturation,
                reasons,
            )
            if saturation_metrics is not None:
                saturated_factor_count += 1
                maximum_sound_p95 = max(maximum_sound_p95, saturation_metrics["sound_p95_ms"])
                maximum_sound_p99 = max(maximum_sound_p99, saturation_metrics["sound_p99_ms"])
                maximum_text_p95 = max(maximum_text_p95, saturation_metrics["text_p95_ms"])
                maximum_text_p99 = max(maximum_text_p99, saturation_metrics["text_p99_ms"])
                maximum_ui_p95 = max(maximum_ui_p95, saturation_metrics["ui_p95_ms"])
                maximum_ui_p99 = max(maximum_ui_p99, saturation_metrics["ui_p99_ms"])
        else:
            key = (topology, window, receivers)
            if key in seen_live_matrix:
                reasons.append("program_benchmark_duplicate_measurement")
            seen_live_matrix.add(key)
            if factor != 1:
                reasons.append("program_benchmark_live_factor_invalid")
            if row["ordinary"].get("offline_saturation") is not None or row["semantic"].get(
                "offline_saturation"
            ) is not None:
                reasons.append("program_benchmark_live_saturation_shape_invalid")
            if all(
                availability[name] == "measured"
                for availability in (ordinary_availability, semantic_availability)
                for name in ("latency_p50_ms", "latency_p95_ms", "latency_p99_ms", "ram_bytes")
            ):
                if not _ordered_percentiles(ordinary) or not _ordered_percentiles(semantic):
                    reasons.append("program_benchmark_percentile_order_invalid")
                p95_ratio = _ratio_micros(
                    _required_metric(semantic, "latency_p95_ms"),
                    _required_metric(ordinary, "latency_p95_ms"),
                )
                p99_ratio = _ratio_micros(
                    _required_metric(semantic, "latency_p99_ms"),
                    _required_metric(ordinary, "latency_p99_ms"),
                )
                maximum_p95_ratio = max(maximum_p95_ratio, p95_ratio)
                maximum_p99_ratio = max(maximum_p99_ratio, p99_ratio)
                growth = max(0, _required_metric(semantic, "ram_bytes") - _required_metric(ordinary, "ram_bytes"))
                maximum_growth = max(maximum_growth, growth // receivers)

    required_live_matrix = set(product(("pair", "group", "evidence"), WINDOWS, (2, 10, 100)))
    if seen_live_matrix != required_live_matrix:
        reasons.append("program_benchmark_live_matrix_incomplete")
    if seen_offline_matrix != set(OFFLINE_FACTORS):
        reasons.append("program_benchmark_offline_matrix_incomplete")
    if maximum_p95_ratio > V2_THRESHOLDS["maximum_live_p95_ratio_micros"]:
        reasons.append("program_benchmark_live_p95_regression")
    if maximum_p99_ratio > V2_THRESHOLDS["maximum_live_p99_ratio_micros"]:
        reasons.append("program_benchmark_live_p99_regression")
    if maximum_growth > V2_THRESHOLDS["maximum_resource_growth_per_receiver_bytes"]:
        reasons.append("program_benchmark_resource_growth_unbounded")
    if saturated_factor_count != len(OFFLINE_FACTORS):
        reasons.append("program_benchmark_offline_saturation_matrix_incomplete")
    report_sha256 = canonical_sha256(report)
    source_digest = _gate_source_digest()
    measurements: dict[str, int | bool | str] = {
        "verified_runs": 1,
        "input_report_sha256": report_sha256,
        "row_count": len(rows),
        "quality_failure_count": quality_failures,
        "unavailable_metric_count": len(unavailable_metrics),
        "maximum_p95_ratio_micros": maximum_p95_ratio,
        "maximum_p99_ratio_micros": maximum_p99_ratio,
        "maximum_growth_per_receiver_bytes": maximum_growth,
        "offline_saturated_factor_count": saturated_factor_count,
        "maximum_offline_sound_p95_ms": maximum_sound_p95,
        "maximum_offline_sound_p99_ms": maximum_sound_p99,
        "maximum_offline_text_p95_ms": maximum_text_p95,
        "maximum_offline_text_p99_ms": maximum_text_p99,
        "maximum_offline_ui_p95_ms": maximum_ui_p95,
        "maximum_offline_ui_p99_ms": maximum_ui_p99,
    }
    evidence = GateEvidence(
        "ASMP-QA-009",
        "passed" if not reasons else "failed",
        tuple(sorted(set(reasons))),
        source_digest,
        canonical_sha256(
            {
                "input_report_sha256": report_sha256,
                "run_config": config,
                "thresholds": V2_THRESHOLDS,
            }
        ),
        measurements,
    )
    return evidence, measurements


def _metrics_v2(
    value: Any,
    *,
    expected_binding: str,
    topology: str,
    reasons: list[str],
) -> tuple[dict[str, int | None], dict[str, str]]:
    if not isinstance(value, Mapping) or set(value) != V2_MODE_FIELDS:
        raise ProgramEvidenceError("program_benchmark_metrics_invalid")
    if value.get("binding_sha256") != expected_binding:
        reasons.append("program_benchmark_mode_binding_mismatch")
    values = value.get("values")
    availability = value.get("availability")
    if not isinstance(values, Mapping) or set(values) != set(METRIC_FIELDS):
        raise ProgramEvidenceError("program_benchmark_metrics_invalid")
    if not isinstance(availability, Mapping) or set(availability) != set(METRIC_FIELDS):
        raise ProgramEvidenceError("program_benchmark_metric_availability_invalid")
    parsed_values: dict[str, int | None] = {}
    parsed_availability: dict[str, str] = {}
    for name in METRIC_FIELDS:
        state = availability[name]
        if not isinstance(state, Mapping) or set(state) != {"status", "method", "reason_code"}:
            raise ProgramEvidenceError("program_benchmark_metric_availability_invalid")
        status = state.get("status")
        if status not in {"measured", "unavailable", "not_applicable"}:
            raise ProgramEvidenceError("program_benchmark_metric_availability_invalid")
        if not all(isinstance(state.get(field), str) for field in ("method", "reason_code")):
            raise ProgramEvidenceError("program_benchmark_metric_availability_invalid")
        raw = values[name]
        if status == "measured":
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
                raise ProgramEvidenceError("program_benchmark_metric_invalid")
            if not state["method"] or state["reason_code"]:
                raise ProgramEvidenceError("program_benchmark_metric_availability_invalid")
            parsed_values[name] = raw
        else:
            if raw is not None or not state["reason_code"]:
                raise ProgramEvidenceError("program_benchmark_metric_availability_invalid")
            parsed_values[name] = None
        parsed_availability[name] = str(status)
    for field in ("latency_sample_count", "expected_deliveries", "completed_deliveries", "valid_deliveries"):
        if isinstance(value[field], bool) or not isinstance(value[field], int) or value[field] < 0:
            raise ProgramEvidenceError("program_benchmark_delivery_metrics_invalid")
    if value["latency_sample_count"] < EXECUTION_POLICY["execution_limits"]["minimum_latency_samples"]:
        reasons.append("program_benchmark_latency_samples_insufficient")
    if not 0 <= value["valid_deliveries"] <= value["completed_deliveries"] <= value["expected_deliveries"]:
        reasons.append("program_benchmark_delivery_metrics_invalid")
    loss_micros = (
        (value["expected_deliveries"] - value["completed_deliveries"]) * 1_000_000
        // max(1, value["expected_deliveries"])
    )
    if loss_micros > V2_THRESHOLDS["maximum_packet_loss_micros"]:
        reasons.append("program_benchmark_packet_loss_exceeded")
    if topology == "offline" and value["completed_deliveries"] != value["expected_deliveries"]:
        reasons.append("program_benchmark_offline_work_incomplete")
    return parsed_values, parsed_availability


def _validate_v2_config_values(config: Mapping[str, Any]) -> None:
    for name in ("duration_seconds", "width", "height", "framerate", "seed", "timeout_seconds"):
        if isinstance(config[name], bool) or not isinstance(config[name], int) or config[name] < 1:
            raise ProgramEvidenceError("program_benchmark_config_invalid")
    if not isinstance(config["audio_format"], str) or not isinstance(config["execution_mode"], str):
        raise ProgramEvidenceError("program_benchmark_config_invalid")
    profiles = config["network_profiles"]
    if not isinstance(profiles, list) or not profiles or any(not isinstance(value, str) for value in profiles):
        raise ProgramEvidenceError("program_benchmark_config_invalid")
    bindings = config["quality_bindings"]
    if not isinstance(bindings, Mapping) or set(bindings) != TOPOLOGIES:
        raise ProgramEvidenceError("program_benchmark_config_invalid")
    for value in bindings.values():
        if not isinstance(value, Mapping) or set(value) != {"status", "evidence_sha256"}:
            raise ProgramEvidenceError("program_benchmark_config_invalid")
        if value["status"] not in {"passed", "failed", "unavailable"}:
            raise ProgramEvidenceError("program_benchmark_config_invalid")
        digest = value["evidence_sha256"]
        if value["status"] == "unavailable":
            if digest is not None:
                raise ProgramEvidenceError("program_benchmark_config_invalid")
        else:
            _digest(digest)


def _validate_metric_availability(
    *,
    topology: str,
    ordinary: Mapping[str, str],
    semantic: Mapping[str, str],
    unavailable_metrics: set[str],
    reasons: list[str],
) -> None:
    network = {"ingress_bytes", "egress_bytes", "turn_bytes"}
    for name in METRIC_FIELDS:
        statuses = {ordinary[name], semantic[name]}
        if topology == "offline" and name in network:
            if statuses != {"not_applicable"}:
                reasons.append("program_benchmark_offline_network_telemetry_invalid")
            continue
        if statuses != {"measured"}:
            unavailable_metrics.add(name)
            reasons.append(f"program_benchmark_{name}_telemetry_unavailable")


def _quality_v2(value: Any, reasons: list[str]) -> bool:
    fields = {
        "ordinary_score_micros",
        "semantic_score_micros",
        "minimum_score_micros",
        "external_gate_passed",
        "passed",
        "external_evidence_sha256",
        "decision_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ProgramEvidenceError("program_benchmark_quality_invalid")
    for field in ("ordinary_score_micros", "semantic_score_micros", "minimum_score_micros"):
        if isinstance(value[field], bool) or not isinstance(value[field], int) or not 0 <= value[field] <= 1_000_000:
            raise ProgramEvidenceError("program_benchmark_quality_invalid")
    if type(value["external_gate_passed"]) is not bool or type(value["passed"]) is not bool:
        raise ProgramEvidenceError("program_benchmark_quality_invalid")
    if value["external_evidence_sha256"] is not None:
        _digest(value["external_evidence_sha256"])
    decision = {name: value[name] for name in fields - {"decision_sha256"}}
    _digest(value["decision_sha256"])
    if value["decision_sha256"] != canonical_sha256(decision):
        reasons.append("program_benchmark_quality_binding_mismatch")
    expected = (
        value["ordinary_score_micros"] >= value["minimum_score_micros"]
        and value["semantic_score_micros"] >= value["minimum_score_micros"]
        and value["external_gate_passed"] is True
    )
    if value["passed"] is not expected:
        reasons.append("program_benchmark_quality_decision_inconsistent")
    if value["minimum_score_micros"] != V2_THRESHOLDS["minimum_quality_score_micros"]:
        reasons.append("program_benchmark_quality_threshold_stale")
    return value["passed"] is True and expected


def _validate_offline_saturation(
    ordinary: Any,
    semantic: Any,
    reasons: list[str],
) -> dict[str, int] | None:
    fields = {
        "saturation_active",
        "resolver_cycles",
        "resource_policy_admitted",
        "live_pressure_blocked",
        "foreground_pressure_blocked",
        "budget_overrun_blocked",
        "resource_limit_stopped",
        "iterations",
        "sound_p50_ms",
        "sound_p95_ms",
        "sound_p99_ms",
        "text_p50_ms",
        "text_p95_ms",
        "text_p99_ms",
        "ui_p50_ms",
        "ui_p95_ms",
        "ui_p99_ms",
        "projection_count",
        "probe_cpu_micros",
        "probe_ram_bytes",
    }
    if not isinstance(ordinary, Mapping) or not isinstance(semantic, Mapping):
        reasons.append("program_benchmark_offline_saturation_missing")
        return None
    if set(ordinary) != fields or set(semantic) != fields:
        reasons.append("program_benchmark_offline_saturation_shape_invalid")
        return None
    bool_fields = {
        "saturation_active",
        "resource_policy_admitted",
        "live_pressure_blocked",
        "foreground_pressure_blocked",
        "budget_overrun_blocked",
        "resource_limit_stopped",
    }
    for row in (ordinary, semantic):
        if any(type(row[name]) is not bool for name in bool_fields):
            reasons.append("program_benchmark_offline_saturation_shape_invalid")
            return None
        if any(type(row[name]) is not int or row[name] < 0 for name in fields - bool_fields):
            reasons.append("program_benchmark_offline_saturation_shape_invalid")
            return None
        if not all(
            row[name] is True
            for name in (
                "resource_policy_admitted",
                "live_pressure_blocked",
                "foreground_pressure_blocked",
                "budget_overrun_blocked",
                "resource_limit_stopped",
            )
        ):
            reasons.append("program_benchmark_offline_budget_guard_failed")
    if ordinary["saturation_active"] is not False or ordinary["resolver_cycles"] != 0:
        reasons.append("program_benchmark_offline_baseline_invalid")
    if semantic["saturation_active"] is not True or semantic["resolver_cycles"] < 1:
        reasons.append("program_benchmark_offline_saturation_not_measured")
    for prefix in ("sound", "text", "ui"):
        if not semantic[f"{prefix}_p50_ms"] <= semantic[f"{prefix}_p95_ms"] <= semantic[f"{prefix}_p99_ms"]:
            reasons.append("program_benchmark_offline_percentile_order_invalid")
        for percentile in ("p95", "p99"):
            threshold = V2_THRESHOLDS[f"offline_{prefix}_{percentile}_ms"]
            if semantic[f"{prefix}_{percentile}_ms"] > threshold:
                reasons.append(f"program_benchmark_offline_{prefix}_{percentile}_regression")
    if semantic["projection_count"] < semantic["iterations"]:
        reasons.append("program_benchmark_offline_ui_projection_incomplete")
    return {
        name: int(semantic[name])
        for name in (
            "sound_p95_ms",
            "sound_p99_ms",
            "text_p95_ms",
            "text_p99_ms",
            "ui_p95_ms",
            "ui_p99_ms",
        )
    }


def _required_metric(values: Mapping[str, int | None], name: str) -> int:
    value = values[name]
    if not isinstance(value, int):
        raise ProgramEvidenceError("program_benchmark_metric_unavailable")
    return value


def _policy_sha256() -> str:
    return source_hash(ROOT, ("config/semantic-media-program-benchmark.v1.json",))


def _gate_source_digest() -> str:
    return source_hash(
        ROOT,
        (*PRODUCT_SOURCE_PATHS, "docs/benchmarks/semantic-media-program-methodology.md"),
    )


def _assert_measurement_report_content_free(value: Any, path: tuple[str, ...] = ()) -> None:
    """Reject retained media/user data while allowing bounded methodology metadata."""

    forbidden_keys = {
        "ciphertext",
        "content",
        "frame_bytes",
        "key_material",
        "local_path",
        "payload",
        "plaintext",
        "prompt",
        "raw_text",
        "secret",
        "token_value",
        "transcript",
    }
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).casefold() in forbidden_keys:
                raise ProgramEvidenceError(f"program_benchmark_content_field_forbidden:{'.'.join((*path, str(key)))}")
            _assert_measurement_report_content_free(nested, (*path, str(key)))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_measurement_report_content_free(nested, (*path, str(index)))
    elif isinstance(value, str):
        if len(value) > 512 or value.startswith(("/", "file:", "~")) or "\\" in value:
            raise ProgramEvidenceError("program_benchmark_content_value_forbidden")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ProgramEvidenceError("program_benchmark_metric_invalid")


def unavailable() -> GateEvidence:
    source_digest = _gate_source_digest()
    return unavailable_evidence(
        "ASMP-QA-009",
        source_sha256=source_digest,
        config_sha256=canonical_sha256(V2_THRESHOLDS),
        reason_code="program_benchmark_measurements_unavailable",
    )


def _metrics(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != METRIC_FIELDS:
        raise ProgramEvidenceError("program_benchmark_metrics_invalid")
    result: dict[str, int] = {}
    for name, raw in value.items():
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw) or raw < 0:
            raise ProgramEvidenceError("program_benchmark_metric_invalid")
        result[str(name)] = int(raw)
    return result


def _ratio_micros(value: int, baseline: int) -> int:
    if baseline <= 0:
        raise ProgramEvidenceError("program_benchmark_baseline_invalid")
    return value * 1_000_000 // baseline


def _positive_integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProgramEvidenceError("program_benchmark_dimension_invalid")
    return value


def _ordered_percentiles(metrics: Mapping[str, int]) -> bool:
    return metrics["latency_p50_ms"] <= metrics["latency_p95_ms"] <= metrics["latency_p99_ms"]


def _digest(value: Any) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ProgramEvidenceError("program_benchmark_digest_invalid")


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", type=Path)
    source.add_argument(
        "--execute",
        action="store_true",
        help="Execute the bounded local product-contract benchmark matrix.",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/test-gates/semantic-media-performance.json")
    parser.add_argument(
        "--details-output",
        type=Path,
        default=ROOT / "artifacts/domain/semantic-media-program-benchmark.json",
    )
    parser.add_argument("--timeout-seconds", type=int)
    args = parser.parse_args()
    try:
        if args.execute:
            report = run_benchmark(timeout_seconds=args.timeout_seconds)
            _write_input_report(args.details_output, report)
            evidence = evaluate(report)[0]
        elif args.input is not None:
            evidence = evaluate(json.loads(args.input.read_text(encoding="utf-8")))[0]
        else:
            evidence = unavailable()
    except (
        OSError,
        json.JSONDecodeError,
        ProgramBenchmarkExecutionError,
        ProgramEvidenceError,
        ValueError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "reason_code": getattr(exc, "reason_code", "program_benchmark_input_invalid"),
                },
                sort_keys=True,
            )
        )
        return 1
    write_report(args.output, evidence)
    print(json.dumps(evidence.as_document(), sort_keys=True))
    return 0 if evidence.status == "passed" else 1


def _write_input_report(path: Path, report: Mapping[str, Any]) -> None:
    _assert_measurement_report_content_free(report)
    rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
