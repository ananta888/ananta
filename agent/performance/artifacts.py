"""Shared lightweight builders for performance experiment artifacts."""

from __future__ import annotations

import hashlib
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def safe_ref_text(text: str, *, max_chars: int = 4000) -> tuple[str, bool]:
    value = str(text or "")
    if len(value) <= max_chars:
        return value, False
    return value[: max(1, max_chars - 14)].rstrip() + "\n\n[truncated]", True


def hardware_fingerprint() -> dict[str, Any]:
    return {
        "schema": "hardware_fingerprint.v1",
        "machine": platform.machine(),
        "processor": platform.processor()[:120],
        "cpu_count": None,
    }


def software_fingerprint(*, workspace: str | Path | None = None) -> dict[str, Any]:
    return {
        "schema": "software_fingerprint.v1",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "workspace_hash": stable_hash(str(Path(workspace).resolve())) if workspace else "",
    }


def build_benchmark_run_artifact(
    *,
    run_id: str,
    task_id: str,
    profile_id: str,
    command: str,
    cwd: str,
    started_at: str,
    duration_seconds: float,
    exit_code: int,
    metrics: dict[str, Any] | None = None,
    stdout_ref: str = "",
    stderr_ref: str = "",
    artifacts: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    status: str | None = None,
    reason_code: str | None = None,
    env_sanitized: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "schema": "benchmark_run_artifact.v1",
        "run_id": run_id,
        "task_id": task_id,
        "profile_id": profile_id,
        "command": command,
        "cwd": cwd,
        "env_sanitized": dict(env_sanitized or {}),
        "started_at": started_at,
        "duration_seconds": round(float(duration_seconds), 6),
        "exit_code": int(exit_code),
        "status": status or ("completed" if int(exit_code) == 0 else "failed"),
        "reason_code": reason_code or ("success" if int(exit_code) == 0 else "command_failed"),
        "metrics": dict(metrics or {}),
        "stdout_ref": stdout_ref,
        "stderr_ref": stderr_ref,
        "artifacts": list(artifacts or []),
        "hardware_fingerprint": hardware_fingerprint(),
        "software_fingerprint": software_fingerprint(workspace=cwd),
        "warnings": list(warnings or []),
    }


def metric_samples(run: dict[str, Any], metric: str) -> list[float]:
    metrics = dict(run.get("metrics") or {})
    value = metrics.get(metric)
    numeric = (int, float)
    if isinstance(value, dict):
        samples = value.get("samples")
        if isinstance(samples, list):
            return [float(x) for x in samples if isinstance(x, numeric) and not isinstance(x, bool)]
        for key in ("median", "value", "mean"):
            if isinstance(value.get(key), numeric) and not isinstance(value.get(key), bool):
                return [float(value[key])]
    if isinstance(value, list):
        return [float(x) for x in value if isinstance(x, numeric) and not isinstance(x, bool)]
    if isinstance(value, numeric) and not isinstance(value, bool):
        return [float(value)]
    return []


def metric_value(run: dict[str, Any], metric: str) -> float | None:
    samples = metric_samples(run, metric)
    if samples:
        return float(median(samples))
    if metric == "wall_time":
        return float(run.get("duration_seconds") or 0.0)
    return None


def build_performance_comparison_artifact(
    *,
    baseline_run: dict[str, Any],
    candidate_run: dict[str, Any],
    metric: str = "wall_time",
    min_relative_improvement_percent: float = 5.0,
    regression_passed: bool = True,
) -> dict[str, Any]:
    baseline_value = metric_value(baseline_run, metric)
    candidate_value = metric_value(candidate_run, metric)
    caveats: list[str] = []
    absolute_delta = None
    relative_delta = None
    if baseline_value is None or candidate_value is None or baseline_value <= 0:
        pass_fail = "inconclusive"
        reason_code = "missing_comparable_metric"
        confidence = 0.0
    else:
        absolute_delta = candidate_value - baseline_value
        relative_delta = (absolute_delta / baseline_value) * 100.0
        improved = relative_delta <= -abs(min_relative_improvement_percent)
        worsened = relative_delta > 0
        if not regression_passed:
            pass_fail = "rejected"
            reason_code = "regression_failed"
        elif improved:
            pass_fail = "passed"
            reason_code = "improvement_above_threshold"
        elif worsened:
            pass_fail = "rejected"
            reason_code = "performance_regressed"
        else:
            pass_fail = "inconclusive"
            reason_code = "delta_below_threshold"
        confidence = 0.8 if pass_fail in {"passed", "rejected"} else 0.4
    if not regression_passed:
        caveats.append("regression gate did not pass")
    comparison_id = f"cmp-{stable_hash([baseline_run.get('run_id'), candidate_run.get('run_id'), time.time_ns()])[:16]}"
    return {
        "schema": "performance_comparison_artifact.v1",
        "comparison_id": comparison_id,
        "baseline_run_id": baseline_run.get("run_id"),
        "candidate_run_id": candidate_run.get("run_id"),
        "metric_deltas": {
            metric: {
                "baseline": baseline_value,
                "candidate": candidate_value,
                "absolute_delta": absolute_delta,
                "relative_delta_percent": relative_delta,
            }
        },
        "confidence": confidence,
        "noise_estimate": {"method": "single_or_median", "value": 0.0},
        "pass_fail": pass_fail,
        "reason_code": reason_code,
        "code_delta": candidate_run.get("code_delta", {}),
        "config_delta": candidate_run.get("config_delta", {}),
        "data_delta": candidate_run.get("data_delta", {}),
        "hardware_delta": {},
        "caveats": caveats,
        "created_at": utc_now(),
    }


def build_optimization_hypothesis_artifact(
    *,
    hypothesis_id: str,
    hotspot_refs: list[dict[str, Any]],
    suspected_bottleneck: str,
    expected_effect: str,
    affected_files: list[str],
    risk: str,
    required_measurements: list[str],
    falsification_criteria: list[str],
) -> dict[str, Any]:
    if not hotspot_refs:
        raise ValueError("optimization hypothesis requires at least one evidence/hotspot ref")
    return {
        "schema": "optimization_hypothesis_artifact.v1",
        "hypothesis_id": hypothesis_id,
        "hotspot_refs": hotspot_refs,
        "suspected_bottleneck": suspected_bottleneck,
        "expected_effect": expected_effect,
        "affected_files": affected_files,
        "risk": risk,
        "required_measurements": required_measurements,
        "falsification_criteria": falsification_criteria,
        "created_at": utc_now(),
    }


def build_experiment_plan_artifact(
    *,
    plan_id: str,
    steps: list[dict[str, Any]],
    required_tools: list[str],
    patch_strategy: dict[str, Any] | None = None,
    benchmark_matrix: list[dict[str, Any]] | None = None,
    regression_matrix: list[dict[str, Any]] | None = None,
    rollback_plan: dict[str, Any] | None = None,
    human_review_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": "experiment_plan_artifact.v1",
        "plan_id": plan_id,
        "steps": steps,
        "required_tools": required_tools,
        "patch_strategy": dict(patch_strategy or {"mode": "proposal_only"}),
        "benchmark_matrix": list(benchmark_matrix or []),
        "regression_matrix": list(regression_matrix or []),
        "rollback_plan": dict(rollback_plan or {"mode": "discard_sandbox"}),
        "human_review_gate": dict(human_review_gate or {"required": True}),
        "created_at": utc_now(),
    }
