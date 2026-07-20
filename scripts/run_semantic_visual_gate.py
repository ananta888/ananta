#!/usr/bin/env python3
"""Fail-closed semantic visual release gate.

The gate is evaluation-only: it never changes Hub flags.  A Hub deployment may
consume a passing artifact; the current spike is a declared NO-GO and therefore
keeps all active visual paths disabled while Ordinary remains available.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from agent.services.semantic_media_program_evidence import (
    ProgramEvidenceError,
    canonical_sha256,
    source_hash,
    verify_bound_report,
)

try:
    from scripts.e2e.semantic_media_e2e_report import playwright_gate_config
except ModuleNotFoundError:  # Direct execution sets scripts as sys.path[0].
    from e2e.semantic_media_e2e_report import playwright_gate_config

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPIKE = ROOT / "artifacts/domain/semantic-visual-feasibility.json"
DEFAULT_BENCHMARK = ROOT / "artifacts/domain/semantic-visual-benchmark.json"
DEFAULT_LIFECYCLE_E2E = ROOT / "artifacts/e2e/semantic-visual-lifecycle-report.json"
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/semantic-visual.json"
VISUAL_LIFECYCLE_SPEC = "semantic-visual-lifecycle.spec.ts"
VISUAL_LIFECYCLE_SOURCE_PATHS = (
    "agent/services/semantic_media_program_evidence.py",
    "scripts/e2e/semantic_media_e2e_report.py",
    "frontend-angular/tests/semantic-visual-lifecycle.spec.ts",
    "frontend-angular/src/main.ts",
    "frontend-angular/src/app/e2e/semantic-visual-lifecycle-live-driver.ts",
    "frontend-angular/src/app/services/semantic-visual-controller.service.ts",
    "frontend-angular/src/app/services/semantic-visual-fallback.service.ts",
    "frontend-angular/src/app/services/semantic-recovery.service.ts",
)

PREDECLARED_THRESHOLDS = {
    "safety": {"minimum_psnr_db": 30, "maximum_drift_mae": 6},
    "go": {
        "suitable_scenario_byte_ratio": 0.7,
        "maximum_camera_byte_ratio": 1.1,
        "maximum_noise_byte_ratio": 1.25,
        "maximum_cpu_ratio": 2,
        "maximum_memory_bytes": 64 * 1024 * 1024,
        "maximum_latency_ratio": 1.25,
        "minimum_beneficial_scenarios": 3,
    },
    "no_go": {
        "aggregate_byte_ratio": 1.25,
        "maximum_cpu_ratio": 3,
        "maximum_memory_bytes": 128 * 1024 * 1024,
        "maximum_latency_ratio": 1.75,
        "minimum_beneficial_scenarios": 2,
    },
}
BENCHMARK_THRESHOLDS = {
    "maximum_mean_byte_ratio": 0.7,
    "maximum_p95_byte_ratio": 1.25,
    "maximum_working_bytes": 64 * 1024 * 1024,
    "maximum_worst_burst_bytes": 512 * 1024,
    "maximum_p95_latency_ms": 20_000,
}


def evaluate_visual_gate(
    spike: Mapping[str, Any],
    benchmark: Mapping[str, Any],
    lifecycle_e2e: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    decision = spike.get("decision")
    thresholds = spike.get("thresholds")
    recomputed = _recompute_spike(spike)
    if not isinstance(decision, Mapping) or not isinstance(thresholds, Mapping):
        reasons.append("spike_contract_invalid")
    elif thresholds != PREDECLARED_THRESHOLDS:
        reasons.append("spike_thresholds_changed")
    elif recomputed is None:
        reasons.append("spike_measurements_invalid")
    elif (
        decision.get("verdict") != recomputed["verdict"]
        or decision.get("activation_allowed") != recomputed["activation_allowed"]
    ):
        reasons.append("spike_decision_inconsistent")
    if recomputed is None or recomputed.get("verdict") != "go" or recomputed.get("activation_allowed") is not True:
        reasons.append("spike_no_go")
    if benchmark.get("schema") != "ananta.semantic-visual-benchmark.v1":
        reasons.append("benchmark_contract_invalid")
    if benchmark.get("windows_seconds") != [2, 10, 20] or benchmark.get("participant_counts") != [2, 10]:
        reasons.append("benchmark_matrix_incomplete")
    scenarios = benchmark.get("scenarios")
    required_scenarios = {"static_ui", "text_scroll", "cursor_animation", "camera", "scene_cut", "strong_noise"}
    if not isinstance(scenarios, list) or set(scenarios) != required_scenarios:
        reasons.append("benchmark_scenarios_incomplete")
    rows = benchmark.get("rows")
    if not isinstance(rows, list) or len(rows) != 6:
        reasons.append("benchmark_rows_invalid")
    else:
        maximum_mean_ratio = 0.0
        maximum_p95_ratio = 0.0
        maximum_memory = 0
        maximum_burst = 0
        maximum_latency = 0.0
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != {
                "participants",
                "window_seconds",
                "samples",
                "byte_ratio",
                "latency_ms",
                "worst_burst_bytes",
                "maximum_working_bytes",
            }:
                reasons.append("benchmark_row_shape_invalid")
                break
            for metric in ("byte_ratio", "latency_ms"):
                summary = row.get(metric)
                if not isinstance(summary, Mapping) or set(summary) != {"mean", "p95"}:
                    reasons.append("benchmark_distribution_missing")
                    break
            try:
                maximum_mean_ratio = max(maximum_mean_ratio, float(row["byte_ratio"]["mean"]))
                maximum_p95_ratio = max(maximum_p95_ratio, float(row["byte_ratio"]["p95"]))
                maximum_memory = max(maximum_memory, int(row["maximum_working_bytes"]))
                maximum_burst = max(maximum_burst, int(row["worst_burst_bytes"]))
                maximum_latency = max(maximum_latency, float(row["latency_ms"]["p95"]))
            except (KeyError, TypeError, ValueError):
                reasons.append("benchmark_value_invalid")
                break
        if maximum_mean_ratio > BENCHMARK_THRESHOLDS["maximum_mean_byte_ratio"]:
            reasons.append("benchmark_byte_benefit_missing")
        if maximum_p95_ratio > BENCHMARK_THRESHOLDS["maximum_p95_byte_ratio"]:
            reasons.append("benchmark_p95_byte_burst_exceeded")
        if maximum_memory > BENCHMARK_THRESHOLDS["maximum_working_bytes"]:
            reasons.append("benchmark_memory_exceeded")
        if maximum_burst > BENCHMARK_THRESHOLDS["maximum_worst_burst_bytes"]:
            reasons.append("benchmark_burst_exceeded")
        if maximum_latency > BENCHMARK_THRESHOLDS["maximum_p95_latency_ms"]:
            reasons.append("benchmark_latency_exceeded")
    lifecycle_reasons = _validate_lifecycle_e2e(lifecycle_e2e)
    reasons.extend(lifecycle_reasons)
    lifecycle_passed = not lifecycle_reasons
    passed = not reasons
    return {
        "schema": "ananta.semantic-visual-release-gate.v1",
        "policy_version": "semantic-visual-release-gate/1.0.0",
        "passed": passed,
        "semantic_visual_activation": passed,
        "ordinary_fallback_required": True,
        "lifecycle_e2e_passed": lifecycle_passed,
        "reasons": sorted(set(reasons)),
        "threshold_source": "semantic-visual-feasibility.v1",
        "benchmark_thresholds": BENCHMARK_THRESHOLDS,
    }


def _validate_lifecycle_e2e(report: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(report, Mapping):
        return ["visual_lifecycle_e2e_missing"]
    expected_source = source_hash(ROOT, VISUAL_LIFECYCLE_SOURCE_PATHS)
    expected_config = canonical_sha256(playwright_gate_config(spec=VISUAL_LIFECYCLE_SPEC))
    try:
        evidence = verify_bound_report(
            report,
            expected_gate_id="ASMP-VIS-012",
            expected_source_sha256=expected_source,
            expected_config_sha256=expected_config,
        )
    except ProgramEvidenceError as exc:
        return [str(exc.reason_code)]
    measurements = evidence.measurements
    required_minimums = {
        "browser_count": 2,
        "visual_lifecycle_scenario_count": 2,
        "visual_process_count": 2,
        "visual_engine_count": 2,
        "visual_scenario_min": 6,
        "visual_observe_min": 12,
        "visual_active_min": 12,
        "visual_recovery_min": 12,
        "visual_revoke_min": 12,
        "visual_reconnect_min": 12,
        "visual_ordinary_fallback_min": 12,
        "visual_direct_link_min": 2,
        "visual_ordinary_receiver_min": 2,
    }
    if evidence.status != "passed" or any(
        type(measurements.get(name)) is not int or int(measurements[name]) < minimum
        for name, minimum in required_minimums.items()
    ):
        return ["visual_lifecycle_e2e_incomplete"]
    return []


def _recompute_spike(spike: Mapping[str, Any]) -> dict[str, Any] | None:
    scenarios = spike.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != 6:
        return None
    try:
        by_id = {item["scenario"]: item for item in scenarios}
        if set(by_id) != {"static_ui", "text_scroll", "cursor_animation", "camera", "scene_cut", "strong_noise"}:
            return None
        byte_ratios = [float(item["ratios"]["bytes"]) for item in scenarios]
        cpu_ratios = [float(item["ratios"]["cpu"]) for item in scenarios]
        latency_ratios = [float(network["latency_ratio"]) for item in scenarios for network in item["networks"]]
        memories = [int(item["semantic"]["memory_bytes"]) for item in scenarios]
        psnrs = [float(item["semantic"]["psnr_db"]) for item in scenarios]
        drifts = [float(item["semantic"]["drift_mae"]) for item in scenarios]
    except (KeyError, TypeError, ValueError):
        return None
    values = byte_ratios + cpu_ratios + latency_ratios + memories + psnrs + drifts
    if any(not isinstance(value, (int, float)) or value < 0 for value in values):
        return None
    aggregate = sum(byte_ratios) / len(byte_ratios)
    maximum_cpu = max(cpu_ratios)
    maximum_latency = max(latency_ratios)
    maximum_memory = max(memories)
    minimum_psnr = min(psnrs)
    maximum_drift = max(drifts)
    beneficial = sum(value <= 1 for value in byte_ratios)
    safety = (
        minimum_psnr >= PREDECLARED_THRESHOLDS["safety"]["minimum_psnr_db"]
        and maximum_drift <= PREDECLARED_THRESHOLDS["safety"]["maximum_drift_mae"]
    )
    no_go = (
        not safety
        or aggregate > PREDECLARED_THRESHOLDS["no_go"]["aggregate_byte_ratio"]
        or maximum_cpu > PREDECLARED_THRESHOLDS["no_go"]["maximum_cpu_ratio"]
        or maximum_memory > PREDECLARED_THRESHOLDS["no_go"]["maximum_memory_bytes"]
        or maximum_latency > PREDECLARED_THRESHOLDS["no_go"]["maximum_latency_ratio"]
        or beneficial < PREDECLARED_THRESHOLDS["no_go"]["minimum_beneficial_scenarios"]
    )
    go = (
        safety
        and all(
            by_id[name]["ratios"]["bytes"] <= PREDECLARED_THRESHOLDS["go"]["suitable_scenario_byte_ratio"]
            for name in ("static_ui", "text_scroll", "cursor_animation")
        )
        and by_id["camera"]["ratios"]["bytes"] <= PREDECLARED_THRESHOLDS["go"]["maximum_camera_byte_ratio"]
        and by_id["strong_noise"]["ratios"]["bytes"] <= PREDECLARED_THRESHOLDS["go"]["maximum_noise_byte_ratio"]
        and maximum_cpu <= PREDECLARED_THRESHOLDS["go"]["maximum_cpu_ratio"]
        and maximum_memory <= PREDECLARED_THRESHOLDS["go"]["maximum_memory_bytes"]
        and maximum_latency <= PREDECLARED_THRESHOLDS["go"]["maximum_latency_ratio"]
        and beneficial >= PREDECLARED_THRESHOLDS["go"]["minimum_beneficial_scenarios"]
    )
    return {"verdict": "no_go" if no_go else "go" if go else "conditional_go", "activation_allowed": go}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spike", type=Path, default=DEFAULT_SPIKE)
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--lifecycle-e2e", type=Path, default=DEFAULT_LIFECYCLE_E2E)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--expect-disabled", action="store_true")
    args = parser.parse_args()
    spike_bytes = args.spike.read_bytes()
    benchmark_bytes = args.benchmark.read_bytes()
    try:
        lifecycle_e2e = json.loads(args.lifecycle_e2e.read_bytes())
    except (OSError, json.JSONDecodeError):
        lifecycle_e2e = None
    result = evaluate_visual_gate(
        json.loads(spike_bytes),
        json.loads(benchmark_bytes),
        lifecycle_e2e,
    )
    if json.loads(benchmark_bytes).get("source_spike_sha256") != hashlib.sha256(spike_bytes).hexdigest():
        result["passed"] = False
        result["semantic_visual_activation"] = False
        result["reasons"] = sorted(set(result["reasons"] + ["benchmark_spike_binding_mismatch"]))
    result["inputs"] = {
        "spike_sha256": hashlib.sha256(spike_bytes).hexdigest(),
        "benchmark_sha256": hashlib.sha256(benchmark_bytes).hexdigest(),
        "lifecycle_e2e_sha256": hashlib.sha256(
            json.dumps(lifecycle_e2e, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if lifecycle_e2e is not None
        else None,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    if args.expect_disabled:
        return 0 if result["passed"] is False and result["ordinary_fallback_required"] is True else 1
    return 0 if result["passed"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
