#!/usr/bin/env python3
"""Aggregate real Kanban performance diagnostics into a formal fail-closed gate."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.performance.kanban_baseline_approval_policy import (
        DEFAULT_POLICY,
        validate_policy_approval,
    )
    from scripts.performance.kanban_performance_io import (
        load_json,
        write_json_atomic,
    )
    from scripts.performance.kanban_performance_io import (
        repo_path as _repo_path,
    )
    from scripts.performance.kanban_performance_io import (
        sha256_bytes as _sha256_bytes,
    )
    from scripts.performance.kanban_performance_io import (
        sha256_path as _sha256_path,
    )
    from scripts.performance.kanban_performance_io import (
        source_artifact as _source_artifact,
    )
    from scripts.performance.kanban_performance_validation import (
        SuiteValidationError,
    )
    from scripts.performance.kanban_performance_validation import (
        integer as _integer,
    )
    from scripts.performance.kanban_performance_validation import (
        list_value as _list,
    )
    from scripts.performance.kanban_performance_validation import (
        mapping as _mapping,
    )
    from scripts.performance.kanban_performance_validation import (
        number as _number,
    )
    from scripts.performance.kanban_performance_validation import (
        percentile as _percentile,
    )
    from scripts.performance.kanban_performance_validation import (
        require_false as _require_false,
    )
    from scripts.performance.kanban_performance_validation import (
        text as _text,
    )
except ModuleNotFoundError:
    from kanban_baseline_approval_policy import (  # type: ignore
        DEFAULT_POLICY,
        validate_policy_approval,
    )
    from kanban_performance_io import (  # type: ignore
        load_json,
        write_json_atomic,
    )
    from kanban_performance_io import (
        repo_path as _repo_path,
    )
    from kanban_performance_io import (
        sha256_bytes as _sha256_bytes,
    )
    from kanban_performance_io import (
        sha256_path as _sha256_path,
    )
    from kanban_performance_io import (
        source_artifact as _source_artifact,
    )
    from kanban_performance_validation import (  # type: ignore
        SuiteValidationError,
    )
    from kanban_performance_validation import (
        integer as _integer,
    )
    from kanban_performance_validation import (
        list_value as _list,
    )
    from kanban_performance_validation import (
        mapping as _mapping,
    )
    from kanban_performance_validation import (
        number as _number,
    )
    from kanban_performance_validation import (
        percentile as _percentile,
    )
    from kanban_performance_validation import (
        require_false as _require_false,
    )
    from kanban_performance_validation import (
        text as _text,
    )


ROOT = Path(__file__).resolve().parents[2]
LOCAL_SCOPE = "local_diagnostic_not_release_evidence"
PROFILE_SCHEMA = "ananta.kanban-model-dashboard.performance-profile.v1"
BASELINE_SCHEMA = "ananta.kanban-model-dashboard.performance-baseline.v1"
GATE_SCHEMA = "ananta.kanban-model-dashboard.performance-gate.v1"
DEFAULT_PROFILE = (
    ROOT
    / "config"
    / "test-profiles"
    / "kanban-model-dashboard"
    / "formal-performance.v1.json"
)
DEFAULT_BACKEND = ROOT / "artifacts" / "kanban-local-performance-diagnostic.json"
DEFAULT_ANGULAR = ROOT / "artifacts" / "angular-kanban-local-performance-diagnostic.json"
DEFAULT_TUI = ROOT / "artifacts" / "tui-kanban-local-performance-diagnostic.json"
DEFAULT_PTY = ROOT / "artifacts" / "tui-kanban-pty-resize-local-diagnostic.json"
DEFAULT_CANDIDATE = (
    ROOT
    / "artifacts"
    / "test-gates"
    / "kanban-model-dashboard-performance-baseline-candidate.v1.json"
)
DEFAULT_GATE = (
    ROOT
    / "artifacts"
    / "test-gates"
    / "kanban-model-dashboard-performance-gate.v1.json"
)

REQUIRED_METRICS = (
    "backend.snapshot_p50_ms",
    "backend.snapshot_p95_ms",
    "backend.move_p50_ms",
    "backend.move_p95_ms",
    "angular.render_p50_ms",
    "angular.render_p95_ms",
    "angular.filter_p50_ms",
    "angular.filter_p95_ms",
    "tui.render_p50_ms",
    "tui.render_p95_ms",
    "tui.pty_resize_p50_ms",
    "tui.pty_resize_p95_ms",
    "memory.peak_rss_mb",
    "browser.long_task_count_p50",
    "browser.long_task_count_p95",
    "browser.long_task_total_p95_ms",
    "browser.longest_task_p95_ms",
    "events.observed_rate_per_second",
    "events.lost",
    "events.deduplicated",
)


def validate_profile(profile: dict[str, Any]) -> None:
    if profile.get("schema") != PROFILE_SCHEMA or profile.get("profile_version") != 1:
        raise SuiteValidationError("formal_profile_schema_invalid")
    _text(profile.get("profile_id"), "formal_profile_id")
    workload = _mapping(profile.get("workload"), "formal_profile_workload")
    if _integer(workload.get("task_count"), "task_count") != 1000:
        raise SuiteValidationError("formal_profile_task_count_must_equal_1000")
    columns = _list(workload.get("canonical_columns"), "canonical_columns")
    if columns != ["todo", "in_progress", "blocked", "completed"]:
        raise SuiteValidationError("formal_profile_canonical_columns_invalid")
    statuses = _list(workload.get("status_groups"), "status_groups")
    views = _list(workload.get("view_groups"), "view_groups")
    if len(statuses) < 10 or len(set(statuses)) != len(statuses):
        raise SuiteValidationError("formal_profile_status_groups_invalid")
    if len(views) < 10 or len(set(views)) != len(views):
        raise SuiteValidationError("formal_profile_view_groups_invalid")
    events = _mapping(workload.get("events"), "events")
    if _number(events.get("target_rate_per_second"), "event_rate") != 100.0:
        raise SuiteValidationError("formal_profile_event_rate_must_equal_100")
    if _integer(events.get("unique_count"), "event_unique_count") != 1000:
        raise SuiteValidationError("formal_profile_event_count_must_equal_1000")
    if _integer(events.get("duplicate_every"), "duplicate_every", minimum=1) != 10:
        raise SuiteValidationError("formal_profile_duplicate_interval_invalid")
    expected_sizes = [
        {"columns": 80, "rows": 24},
        {"columns": 120, "rows": 30},
        {"columns": 160, "rows": 40},
    ]
    if workload.get("terminal_sizes") != expected_sizes:
        raise SuiteValidationError("formal_profile_terminal_sizes_invalid")
    if workload.get("angular_viewports") != ["desktop", "mobile"]:
        raise SuiteValidationError("formal_profile_angular_viewports_invalid")

    schemas = _mapping(
        profile.get("required_source_schemas"),
        "required_source_schemas",
    )
    if set(schemas) != {"backend", "angular", "tui", "pty"}:
        raise SuiteValidationError("formal_profile_source_schemas_invalid")
    source_profiles = _mapping(profile.get("source_profiles"), "source_profiles")
    if set(source_profiles) != {"backend", "tui"}:
        raise SuiteValidationError("formal_profile_source_profiles_invalid")

    budgets = _mapping(profile.get("absolute_budgets"), "absolute_budgets")
    if set(budgets) != set(REQUIRED_METRICS):
        raise SuiteValidationError("formal_profile_absolute_budget_set_invalid")
    for metric, raw_budget in budgets.items():
        budget = _mapping(raw_budget, f"budget:{metric}")
        if budget.get("operator") not in {"<=", ">=", "=="}:
            raise SuiteValidationError(f"budget_operator_invalid:{metric}")
        _number(budget.get("value"), f"budget_value:{metric}")

    baseline = _mapping(
        profile.get("baseline_comparison"),
        "baseline_comparison",
    )
    if _number(
        baseline.get("max_regression_percent"),
        "max_regression_percent",
    ) != 15.0:
        raise SuiteValidationError("formal_profile_regression_must_equal_15")
    if baseline.get("required_approval_status") != "approved":
        raise SuiteValidationError("formal_profile_approval_status_invalid")
    if baseline.get("required_approval_method") != "hub_policy":
        raise SuiteValidationError("formal_profile_approval_method_invalid")
    policy_path = _text(
        baseline.get("approval_policy_path"),
        "approval_policy_path",
    )
    if policy_path != _repo_path(DEFAULT_POLICY):
        raise SuiteValidationError("formal_profile_approval_policy_path_invalid")
    approved_path = _text(
        baseline.get("approved_baseline_path"),
        "approved_baseline_path",
    )
    if "candidate" in approved_path or not approved_path.endswith(".json"):
        raise SuiteValidationError("formal_profile_approved_path_invalid")
    directions = _mapping(baseline.get("metrics"), "baseline_metrics")
    if set(directions) != set(REQUIRED_METRICS):
        raise SuiteValidationError("formal_profile_baseline_metric_set_invalid")
    if any(
        direction not in {"lower_is_better", "higher_is_better", "exact"}
        for direction in directions.values()
    ):
        raise SuiteValidationError("formal_profile_baseline_direction_invalid")
    metadata = _list(
        profile.get("required_environment_metadata"),
        "required_environment_metadata",
    )
    if "commit.sha" not in metadata or "browser.version" not in metadata:
        raise SuiteValidationError("formal_profile_metadata_requirements_invalid")


def _verify_source_profile(
    profile: dict[str, Any],
    report: dict[str, Any],
    source: str,
) -> None:
    relative = _text(
        _mapping(profile["source_profiles"], "source_profiles").get(source),
        f"source_profile:{source}",
    )
    expected = _sha256_path(ROOT / relative)
    if report.get("profile_sha256") != expected:
        raise SuiteValidationError(f"{source}_profile_sha256_mismatch")


def _validate_local_flags(
    report: dict[str, Any],
    *,
    source: str,
    scope_field: str = "scope",
) -> None:
    if report.get(scope_field) != LOCAL_SCOPE:
        raise SuiteValidationError(f"{source}_scope_invalid")
    _require_false(report.get("release_evidence"), f"{source}_release_evidence")
    formal_field = "formal" if source == "angular" else "formal_gate_eligible"
    _require_false(report.get(formal_field), f"{source}_{formal_field}")


def normalise_measurements(
    *,
    profile: dict[str, Any],
    backend: dict[str, Any],
    angular: dict[str, Any],
    tui: dict[str, Any],
    pty: dict[str, Any],
) -> tuple[dict[str, float | int], dict[str, Any]]:
    validate_profile(profile)
    schemas = _mapping(profile["required_source_schemas"], "source_schemas")
    workload = _mapping(profile["workload"], "workload")

    if backend.get("schema") != schemas["backend"]:
        raise SuiteValidationError("backend_schema_invalid")
    _validate_local_flags(backend, source="backend")
    _verify_source_profile(profile, backend, "backend")
    if _mapping(
        backend.get("absolute_evaluation"),
        "backend_absolute_evaluation",
    ).get("within_budget") is not True:
        raise SuiteValidationError("backend_local_budget_failed")
    backend_metrics = _mapping(backend.get("measurements"), "backend_measurements")
    if _integer(backend_metrics.get("task_count"), "backend_task_count") != 1000:
        raise SuiteValidationError("backend_task_count_invalid")
    if _integer(backend_metrics.get("status_group_count"), "backend_status_groups") < 10:
        raise SuiteValidationError("backend_status_groups_invalid")
    if _integer(backend_metrics.get("view_group_count"), "backend_view_groups") < 10:
        raise SuiteValidationError("backend_view_groups_invalid")

    if angular.get("schema") != schemas["angular"]:
        raise SuiteValidationError("angular_schema_invalid")
    _validate_local_flags(
        angular,
        source="angular",
        scope_field="evidence_classification",
    )
    dataset = _mapping(angular.get("dataset"), "angular_dataset")
    if (
        _integer(dataset.get("cards"), "angular_cards") != 1000
        or _integer(dataset.get("canonical_columns"), "angular_columns") != 4
        or _integer(dataset.get("status_groups"), "angular_status_groups") < 10
        or _integer(dataset.get("view_groups"), "angular_view_groups") < 10
    ):
        raise SuiteValidationError("angular_workload_invalid")
    methodology = _mapping(angular.get("methodology"), "angular_methodology")
    min_runs = _integer(
        _mapping(profile.get("sampling"), "sampling").get(
            "angular_measured_runs_per_viewport_min"
        ),
        "angular_min_runs",
        minimum=1,
    )
    if _integer(
        methodology.get("measured_runs_per_viewport"),
        "angular_measured_runs",
    ) < min_runs:
        raise SuiteValidationError("angular_sample_count_invalid")
    expected_viewports = set(_list(workload["angular_viewports"], "angular_viewports"))
    actual_viewports: set[str] = set()
    angular_render_p50: list[float] = []
    angular_render_p95: list[float] = []
    angular_filter_p50: list[float] = []
    angular_filter_p95: list[float] = []
    long_count_p50: list[float] = []
    long_count_p95: list[float] = []
    long_total_p95: list[float] = []
    longest_p95: list[float] = []
    retained_heap_mb: list[float] = []
    for index, raw_viewport in enumerate(
        _list(angular.get("viewports"), "angular_viewports")
    ):
        viewport_result = _mapping(raw_viewport, f"angular_viewport:{index}")
        viewport = _mapping(viewport_result.get("viewport"), "angular_viewport")
        name = _text(viewport.get("name"), "angular_viewport_name")
        actual_viewports.add(name)
        summary = _mapping(viewport_result.get("summary"), f"angular_summary:{name}")
        if summary.get("longTaskApiAvailable") is not True:
            raise SuiteValidationError(f"angular_long_task_api_unavailable:{name}")
        if summary.get("jsHeapAvailable") is not True:
            raise SuiteValidationError(f"angular_heap_api_unavailable:{name}")
        samples = _list(viewport_result.get("samples"), f"angular_samples:{name}")
        if len(samples) < min_runs:
            raise SuiteValidationError(f"angular_samples_insufficient:{name}")
        counts = [
            _number(
                _mapping(sample, f"angular_sample:{name}").get("longTaskCount"),
                f"angular_long_task_count:{name}",
            )
            for sample in samples
        ]
        angular_render_p50.append(
            _number(summary.get("initialRenderP50Ms"), f"angular_render_p50:{name}")
        )
        angular_render_p95.append(
            _number(summary.get("initialRenderP95Ms"), f"angular_render_p95:{name}")
        )
        angular_filter_p50.append(
            _number(summary.get("filterP50Ms"), f"angular_filter_p50:{name}")
        )
        angular_filter_p95.append(
            _number(summary.get("filterP95Ms"), f"angular_filter_p95:{name}")
        )
        long_count_p50.append(_percentile(counts, 0.50))
        long_count_p95.append(_percentile(counts, 0.95))
        long_total_p95.append(
            _number(summary.get("longTaskTotalP95Ms"), f"long_task_total:{name}")
        )
        longest_p95.append(
            _number(summary.get("longestTaskP95Ms"), f"longest_task:{name}")
        )
        retained_heap_mb.append(
            _number(summary.get("retainedHeapP95Bytes"), f"retained_heap:{name}")
            / 1048576.0
        )
    if actual_viewports != expected_viewports:
        raise SuiteValidationError("angular_viewport_set_invalid")
    angular_runtime = _mapping(
        angular.get("producer_runtime"),
        "angular_producer_runtime",
    )
    for key in ("node", "playwright", "browser"):
        runtime_item = _mapping(angular_runtime.get(key), f"angular_runtime:{key}")
        _text(runtime_item.get("version"), f"angular_runtime_version:{key}")
    if _mapping(angular_runtime["browser"], "angular_browser").get("name") != "chromium":
        raise SuiteValidationError("angular_browser_name_invalid")

    if tui.get("schema") != schemas["tui"]:
        raise SuiteValidationError("tui_schema_invalid")
    _validate_local_flags(tui, source="tui")
    _verify_source_profile(profile, tui, "tui")
    if _mapping(
        tui.get("absolute_evaluation"),
        "tui_absolute_evaluation",
    ).get("within_budget") is not True:
        raise SuiteValidationError("tui_local_budget_failed")
    tui_metrics = _mapping(tui.get("measurements"), "tui_measurements")
    if _integer(tui_metrics.get("card_count"), "tui_card_count") != 1000:
        raise SuiteValidationError("tui_card_count_invalid")
    if _integer(tui_metrics.get("status_group_count"), "tui_status_groups") < 10:
        raise SuiteValidationError("tui_status_groups_invalid")
    if _integer(tui_metrics.get("view_group_count"), "tui_view_groups") < 10:
        raise SuiteValidationError("tui_view_groups_invalid")
    terminal_metrics = _mapping(
        tui_metrics.get("terminal_sizes"),
        "tui_terminal_sizes",
    )
    expected_size_keys = {
        f"{item['columns']}x{item['rows']}"
        for item in _list(workload["terminal_sizes"], "terminal_sizes")
    }
    if set(terminal_metrics) != expected_size_keys:
        raise SuiteValidationError("tui_terminal_size_set_invalid")
    tui_render_p50 = [
        _number(_mapping(value, key).get("render_tick_p50_ms"), f"{key}:render_p50")
        for key, value in terminal_metrics.items()
    ]
    tui_render_p95 = [
        _number(_mapping(value, key).get("render_tick_p95_ms"), f"{key}:render_p95")
        for key, value in terminal_metrics.items()
    ]
    event_loop = _mapping(tui_metrics.get("event_loop"), "tui_event_loop")
    target_rate = _number(
        _mapping(workload["events"], "events").get("target_rate_per_second"),
        "target_rate",
    )
    if _number(event_loop.get("target_rate_per_second"), "tui_target_rate") != target_rate:
        raise SuiteValidationError("tui_event_target_rate_invalid")
    if _number(event_loop.get("progress_ratio"), "tui_progress_ratio") != 1.0:
        raise SuiteValidationError("tui_event_progress_incomplete")

    if pty.get("schema") != schemas["pty"]:
        raise SuiteValidationError("pty_schema_invalid")
    _validate_local_flags(pty, source="pty")
    if pty.get("diagnostic_status") != "passed_local_pty_resize":
        raise SuiteValidationError("pty_diagnostic_status_invalid")
    if _integer(pty.get("card_count"), "pty_card_count") != 1000:
        raise SuiteValidationError("pty_card_count_invalid")
    if pty.get("terminal_sizes") != workload["terminal_sizes"]:
        raise SuiteValidationError("pty_terminal_sizes_invalid")
    min_pty_samples = _integer(
        _mapping(profile["sampling"], "sampling").get(
            "pty_resize_samples_per_size_min"
        ),
        "pty_min_samples",
        minimum=1,
    )
    resize_results = _list(pty.get("resize_measurements"), "pty_resize_measurements")
    if len(resize_results) != len(expected_size_keys):
        raise SuiteValidationError("pty_resize_size_count_invalid")
    pty_size_keys: set[str] = set()
    pty_p50: list[float] = []
    pty_p95: list[float] = []
    for index, raw_result in enumerate(resize_results):
        result = _mapping(raw_result, f"pty_resize:{index}")
        size_key = (
            f"{_integer(result.get('columns'), 'pty_columns')}"
            f"x{_integer(result.get('rows'), 'pty_rows')}"
        )
        pty_size_keys.add(size_key)
        if result.get("marker_present") is not True or result.get("process_alive") is not True:
            raise SuiteValidationError(f"pty_resize_process_assertion_failed:{size_key}")
        samples = [
            _number(value, f"pty_resize_sample:{size_key}")
            for value in _list(
                result.get("redraw_latency_samples_ms"),
                f"pty_resize_samples:{size_key}",
            )
        ]
        if (
            _integer(result.get("sample_count"), f"pty_sample_count:{size_key}")
            != len(samples)
            or len(samples) < min_pty_samples
        ):
            raise SuiteValidationError(f"pty_resize_samples_insufficient:{size_key}")
        declared_p50 = _number(
            result.get("redraw_latency_p50_ms"),
            f"pty_resize_p50:{size_key}",
        )
        declared_p95 = _number(
            result.get("redraw_latency_p95_ms"),
            f"pty_resize_p95:{size_key}",
        )
        if abs(declared_p50 - _percentile(samples, 0.50)) > 0.001:
            raise SuiteValidationError(f"pty_resize_p50_mismatch:{size_key}")
        if abs(declared_p95 - _percentile(samples, 0.95)) > 0.001:
            raise SuiteValidationError(f"pty_resize_p95_mismatch:{size_key}")
        pty_p50.append(declared_p50)
        pty_p95.append(declared_p95)
    if pty_size_keys != expected_size_keys:
        raise SuiteValidationError("pty_resize_terminal_size_set_invalid")

    backend_event_rate = _number(
        backend_metrics.get("event_rate_per_second"),
        "backend_event_rate",
    )
    tui_event_rate = _number(
        event_loop.get("observed_rate_per_second"),
        "tui_event_rate",
    )
    backend_lost = _integer(backend_metrics.get("lost_events"), "backend_lost_events")
    tui_lost = _integer(event_loop.get("lost_events"), "tui_lost_events")
    deduplicated = _integer(
        backend_metrics.get("deduped_events"),
        "backend_deduplicated_events",
    )
    events_profile = _mapping(workload["events"], "events")
    expected_deduplicated = (
        _integer(events_profile["unique_count"], "event_unique_count")
        // _integer(events_profile["duplicate_every"], "duplicate_every", minimum=1)
    )
    if deduplicated != expected_deduplicated:
        raise SuiteValidationError("event_deduplication_count_invalid")

    measurements: dict[str, float | int] = {
        "backend.snapshot_p50_ms": _number(
            backend_metrics.get("snapshot_p50_ms"),
            "backend_snapshot_p50",
        ),
        "backend.snapshot_p95_ms": _number(
            backend_metrics.get("snapshot_p95_ms"),
            "backend_snapshot_p95",
        ),
        "backend.move_p50_ms": _number(
            backend_metrics.get("move_p50_ms"),
            "backend_move_p50",
        ),
        "backend.move_p95_ms": _number(
            backend_metrics.get("move_p95_ms"),
            "backend_move_p95",
        ),
        "angular.render_p50_ms": max(angular_render_p50),
        "angular.render_p95_ms": max(angular_render_p95),
        "angular.filter_p50_ms": max(angular_filter_p50),
        "angular.filter_p95_ms": max(angular_filter_p95),
        "tui.render_p50_ms": max(tui_render_p50),
        "tui.render_p95_ms": max(tui_render_p95),
        "tui.pty_resize_p50_ms": max(pty_p50),
        "tui.pty_resize_p95_ms": max(pty_p95),
        "memory.peak_rss_mb": max(
            _number(backend_metrics.get("peak_rss_mb"), "backend_peak_rss"),
            _number(tui_metrics.get("peak_rss_mb"), "tui_peak_rss"),
            _number(pty.get("peak_rss_kib"), "pty_peak_rss_kib") / 1024.0,
        ),
        "browser.long_task_count_p50": max(long_count_p50),
        "browser.long_task_count_p95": max(long_count_p95),
        "browser.long_task_total_p95_ms": max(long_total_p95),
        "browser.longest_task_p95_ms": max(longest_p95),
        "events.observed_rate_per_second": min(
            backend_event_rate,
            tui_event_rate,
        ),
        "events.lost": max(backend_lost, tui_lost),
        "events.deduplicated": deduplicated,
    }
    details = {
        "angular_retained_heap_p95_mb": max(retained_heap_mb),
        "event_rates_per_source": {
            "backend": backend_event_rate,
            "tui": tui_event_rate,
        },
        "lost_events_per_source": {
            "backend": backend_lost,
            "tui": tui_lost,
        },
        "angular_runtime": angular_runtime,
    }
    return measurements, details


def evaluate_absolute(
    measurements: dict[str, float | int],
    profile: dict[str, Any],
) -> dict[str, Any]:
    budgets = _mapping(profile.get("absolute_budgets"), "absolute_budgets")
    checks: dict[str, Any] = {}
    for metric in REQUIRED_METRICS:
        if metric not in measurements:
            raise SuiteValidationError(f"measurement_missing:{metric}")
        actual = _number(measurements[metric], f"measurement:{metric}")
        budget = _mapping(budgets.get(metric), f"budget:{metric}")
        expected = _number(budget.get("value"), f"budget_value:{metric}")
        operator = budget.get("operator")
        passed = (
            actual <= expected
            if operator == "<="
            else actual >= expected
            if operator == ">="
            else math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12)
        )
        checks[metric] = {
            "actual": actual,
            "operator": operator,
            "budget": expected,
            "passed": passed,
        }
    return {
        "within_budget": all(check["passed"] for check in checks.values()),
        "checks": checks,
    }


def _command_version(command: list[str], *, cwd: Path = ROOT) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise SuiteValidationError(f"runtime_command_failed:{command[0]}")
    return _text(result.stdout, f"runtime_command_output:{command[0]}")


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                return _text(line.partition(":")[2], "cpu_model")
    except OSError:
        pass
    return _text(platform.processor(), "cpu_model")


def _total_memory_bytes() -> int:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    raise SuiteValidationError("host_total_memory_unavailable")


def collect_environment(angular_runtime: dict[str, Any]) -> dict[str, Any]:
    source_node = _text(
        _mapping(angular_runtime.get("node"), "angular_node").get("version"),
        "angular_node_version",
    )
    source_playwright = _text(
        _mapping(
            angular_runtime.get("playwright"),
            "angular_playwright",
        ).get("version"),
        "angular_playwright_version",
    )
    node = _command_version(["node", "--version"])
    playwright = _command_version(
        [str(ROOT / "frontend-angular" / "node_modules" / ".bin" / "playwright"), "--version"],
        cwd=ROOT / "frontend-angular",
    )
    if node != source_node or playwright != source_playwright:
        raise SuiteValidationError("angular_runtime_environment_mismatch")
    browser = _mapping(angular_runtime.get("browser"), "angular_browser")
    environment = {
        "host": {
            "hostname": _text(platform.node(), "host_hostname"),
            "os": {
                "system": _text(platform.system(), "host_os_system"),
                "release": _text(platform.release(), "host_os_release"),
                "machine": _text(platform.machine(), "host_os_machine"),
            },
            "cpu": {
                "model": _cpu_model(),
                "logical_count": _integer(os.cpu_count(), "cpu_logical_count", minimum=1),
            },
            "memory": {"total_bytes": _total_memory_bytes()},
        },
        "runtimes": {
            "python": {
                "implementation": platform.python_implementation(),
                "version": platform.python_version(),
            },
            "node": {"version": node},
            "playwright": {"version": playwright},
        },
        "browser": {
            "name": _text(browser.get("name"), "browser_name"),
            "version": _text(browser.get("version"), "browser_version"),
        },
    }
    environment["compatibility"] = {
        "os": environment["host"]["os"],
        "cpu": environment["host"]["cpu"],
        "memory": environment["host"]["memory"],
        "python": environment["runtimes"]["python"],
        "node": environment["runtimes"]["node"],
        "playwright": environment["runtimes"]["playwright"],
        "browser": environment["browser"],
    }
    environment["compatibility_sha256"] = _sha256_bytes(
        json.dumps(
            environment["compatibility"],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return environment


def collect_commit(root: Path = ROOT) -> dict[str, str]:
    git_path = root / ".git"
    if git_path.is_file():
        content = git_path.read_text(encoding="utf-8").strip()
        if not content.startswith("gitdir:"):
            raise SuiteValidationError("gitdir_metadata_invalid")
        git_path = (root / content.partition(":")[2].strip()).resolve()
    head = (git_path / "HEAD").read_text(encoding="utf-8").strip()
    reference = "detached"
    if head.startswith("ref:"):
        reference = _text(head.partition(":")[2], "git_head_ref")
        ref_path = git_path / reference
        if ref_path.is_file():
            sha = ref_path.read_text(encoding="utf-8").strip()
        else:
            sha = ""
            packed = git_path / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line and not line.startswith(("#", "^")):
                        candidate, _, candidate_ref = line.partition(" ")
                        if candidate_ref == reference:
                            sha = candidate
                            break
    else:
        sha = head
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise SuiteValidationError("git_commit_sha_invalid")
    return {
        "sha": sha.lower(),
        "ref": reference,
        "source": "git_metadata_files",
    }


def load_suite_inputs(
    *,
    profile_path: Path,
    backend_path: Path,
    angular_path: Path,
    tui_path: Path,
    pty_path: Path,
) -> dict[str, Any]:
    profile, profile_bytes = load_json(profile_path)
    backend, backend_bytes = load_json(backend_path)
    angular, angular_bytes = load_json(angular_path)
    tui, tui_bytes = load_json(tui_path)
    pty, pty_bytes = load_json(pty_path)
    measurements, details = normalise_measurements(
        profile=profile,
        backend=backend,
        angular=angular,
        tui=tui,
        pty=pty,
    )
    return {
        "profile": profile,
        "profile_sha256": _sha256_bytes(profile_bytes),
        "measurements": measurements,
        "details": details,
        "sources": {
            "backend": _source_artifact(backend_path, backend_bytes, backend),
            "angular": _source_artifact(angular_path, angular_bytes, angular),
            "tui": _source_artifact(tui_path, tui_bytes, tui),
            "pty": _source_artifact(pty_path, pty_bytes, pty),
        },
    }


def build_baseline_candidate(
    *,
    profile: dict[str, Any],
    profile_sha256: str,
    measurements: dict[str, float | int],
    details: dict[str, Any],
    sources: dict[str, Any],
    environment: dict[str, Any],
    commit: dict[str, str],
    created_at: str | None = None,
) -> dict[str, Any]:
    absolute = evaluate_absolute(measurements, profile)
    if not absolute["within_budget"]:
        raise SuiteValidationError("candidate_absolute_budget_failed")
    return {
        "schema": BASELINE_SCHEMA,
        "baseline_version": 1,
        "profile": {
            "id": profile["profile_id"],
            "schema": profile["schema"],
            "sha256": profile_sha256,
        },
        "approval_status": "candidate_unapproved",
        "approved_by": None,
        "approved_at": None,
        "candidate_created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "commit": commit,
        "environment": environment,
        "measurements": measurements,
        "measurement_details": details,
        "source_artifacts": sources,
        "absolute_evaluation": absolute,
        "candidate_status": "ready_for_policy_evaluation",
    }


def evaluate_baseline(
    *,
    profile: dict[str, Any],
    profile_sha256: str,
    measurements: dict[str, float | int],
    environment: dict[str, Any],
    baseline: dict[str, Any],
    approval_policy: dict[str, Any] | None = None,
    approval_policy_sha256: str | None = None,
) -> dict[str, Any]:
    if baseline.get("schema") != BASELINE_SCHEMA or baseline.get("baseline_version") != 1:
        raise SuiteValidationError("baseline_schema_invalid")
    baseline_profile = _mapping(baseline.get("profile"), "baseline_profile")
    profile_compatible = (
        baseline_profile.get("id") == profile.get("profile_id")
        and baseline_profile.get("schema") == profile.get("schema")
        and baseline_profile.get("sha256") == profile_sha256
    )
    baseline_environment = _mapping(
        baseline.get("environment"),
        "baseline_environment",
    )
    environment_compatible = (
        baseline_environment.get("compatibility_sha256")
        == environment.get("compatibility_sha256")
        and baseline_environment.get("compatibility")
        == environment.get("compatibility")
    )
    approval_status = str(baseline.get("approval_status") or "")
    approval_valid = bool(
        approval_status == "approved"
        and approval_policy is not None
        and isinstance(approval_policy_sha256, str)
        and validate_policy_approval(
            baseline=baseline,
            policy=approval_policy,
            policy_sha256=approval_policy_sha256,
        )
    )
    prior = _mapping(baseline.get("measurements"), "baseline_measurements")
    comparison = _mapping(profile["baseline_comparison"], "baseline_comparison")
    max_regression = _number(
        comparison.get("max_regression_percent"),
        "max_regression_percent",
    )
    upper_factor = 1.0 + max_regression / 100.0
    lower_factor = 1.0 - max_regression / 100.0
    checks: dict[str, Any] = {}
    for metric, direction in _mapping(
        comparison.get("metrics"),
        "baseline_metric_directions",
    ).items():
        current = _number(measurements.get(metric), f"current:{metric}")
        previous = _number(prior.get(metric), f"baseline:{metric}")
        if direction == "lower_is_better":
            limit = previous * upper_factor
            passed = current <= limit + 1e-12
        elif direction == "higher_is_better":
            limit = previous * lower_factor
            passed = current + 1e-12 >= limit
        else:
            limit = previous
            passed = math.isclose(current, previous, rel_tol=0.0, abs_tol=1e-12)
        checks[metric] = {
            "current": current,
            "baseline": previous,
            "direction": direction,
            "limit": limit,
            "passed": passed,
        }
    return {
        "baseline_schema": baseline["schema"],
        "approval_status": approval_status,
        "required_approval_status": comparison["required_approval_status"],
        "approval_valid": approval_valid,
        "profile_compatible": profile_compatible,
        "environment_compatible": environment_compatible,
        "comparison_computed": True,
        "formal_comparison_eligible": approval_valid,
        "max_regression_percent": max_regression,
        "within_regression_limit": all(check["passed"] for check in checks.values()),
        "checks": checks,
    }


def build_gate_report(
    *,
    profile: dict[str, Any],
    profile_sha256: str,
    measurements: dict[str, float | int],
    details: dict[str, Any],
    sources: dict[str, Any],
    environment: dict[str, Any],
    commit: dict[str, str],
    baseline: dict[str, Any],
    approval_policy: dict[str, Any] | None = None,
    approval_policy_sha256: str | None = None,
    evaluated_at: str | None = None,
) -> dict[str, Any]:
    absolute = evaluate_absolute(measurements, profile)
    baseline_evaluation = evaluate_baseline(
        profile=profile,
        profile_sha256=profile_sha256,
        measurements=measurements,
        environment=environment,
        baseline=baseline,
        approval_policy=approval_policy,
        approval_policy_sha256=approval_policy_sha256,
    )
    blockers: list[dict[str, Any]] = []
    failed_budgets = [
        metric
        for metric, check in absolute["checks"].items()
        if not check["passed"]
    ]
    if failed_budgets:
        blockers.append(
            {"code": "absolute_budget_exceeded", "metrics": failed_budgets}
        )
    if not baseline_evaluation["profile_compatible"]:
        blockers.append({"code": "baseline_profile_mismatch"})
    if not baseline_evaluation["environment_compatible"]:
        blockers.append({"code": "baseline_environment_mismatch"})
    approval_status = baseline_evaluation["approval_status"]
    if approval_status == "candidate_unapproved":
        blockers.append({"code": "baseline_approval_required"})
    elif not baseline_evaluation["approval_valid"]:
        blockers.append({"code": "baseline_approval_invalid"})
    if not baseline_evaluation["within_regression_limit"]:
        failed_regressions = [
            metric
            for metric, check in baseline_evaluation["checks"].items()
            if not check["passed"]
        ]
        blockers.append(
            {
                "code": "baseline_regression_exceeded",
                "metrics": failed_regressions,
            }
        )
    passed = not blockers
    approval_only = (
        len(blockers) == 1
        and blockers[0]["code"] == "baseline_approval_required"
    )
    return {
        "schema": GATE_SCHEMA,
        "suite_id": "kanban-model-dashboard.performance.v1",
        "scope": "formal_performance_gate",
        "status": "passed" if passed else "blocked" if approval_only else "failed",
        "release_evidence": passed,
        "formal_gate_eligible": passed,
        "evidence_classification": (
            "formal_release_evidence"
            if passed
            else "formal_gate_result_not_release_evidence"
        ),
        "evaluated_at": evaluated_at or datetime.now(timezone.utc).isoformat(),
        "profile": {
            "id": profile["profile_id"],
            "schema": profile["schema"],
            "sha256": profile_sha256,
        },
        "commit": commit,
        "environment": environment,
        "measurements": measurements,
        "measurement_details": details,
        "source_artifacts": sources,
        "absolute_evaluation": absolute,
        "baseline_evaluation": baseline_evaluation,
        "blockers": blockers,
    }


def _add_common_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--backend-result", type=Path, default=DEFAULT_BACKEND)
    parser.add_argument("--angular-result", type=Path, default=DEFAULT_ANGULAR)
    parser.add_argument("--tui-result", type=Path, default=DEFAULT_TUI)
    parser.add_argument("--pty-result", type=Path, default=DEFAULT_PTY)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    candidate = subparsers.add_parser(
        "candidate",
        help="Create an unapproved candidate from real diagnostics.",
    )
    _add_common_inputs(candidate)
    candidate.add_argument("--output", type=Path, default=DEFAULT_CANDIDATE)
    evaluate = subparsers.add_parser(
        "evaluate",
        help="Evaluate diagnostics against a versioned baseline.",
    )
    _add_common_inputs(evaluate)
    evaluate.add_argument("--baseline", type=Path, required=True)
    evaluate.add_argument("--approval-policy", type=Path, default=DEFAULT_POLICY)
    evaluate.add_argument("--output", type=Path, default=DEFAULT_GATE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        inputs = load_suite_inputs(
            profile_path=args.profile,
            backend_path=args.backend_result,
            angular_path=args.angular_result,
            tui_path=args.tui_result,
            pty_path=args.pty_result,
        )
        environment = collect_environment(
            _mapping(
                inputs["details"].get("angular_runtime"),
                "angular_runtime",
            )
        )
        commit = collect_commit()
        if args.command == "candidate":
            report = build_baseline_candidate(
                profile=inputs["profile"],
                profile_sha256=inputs["profile_sha256"],
                measurements=inputs["measurements"],
                details=inputs["details"],
                sources=inputs["sources"],
                environment=environment,
                commit=commit,
            )
            exit_code = 0
            status = report["candidate_status"]
        else:
            baseline, _baseline_bytes = load_json(args.baseline)
            approval_policy, approval_policy_bytes = load_json(args.approval_policy)
            report = build_gate_report(
                profile=inputs["profile"],
                profile_sha256=inputs["profile_sha256"],
                measurements=inputs["measurements"],
                details=inputs["details"],
                sources=inputs["sources"],
                environment=environment,
                commit=commit,
                baseline=baseline,
                approval_policy=approval_policy,
                approval_policy_sha256=_sha256_bytes(approval_policy_bytes),
            )
            status = report["status"]
            exit_code = 0 if status == "passed" else 2 if status == "blocked" else 1
        write_json_atomic(args.output, report)
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "status": status,
                    "blockers": report.get("blockers", []),
                },
                sort_keys=True,
            )
        )
        return exit_code
    except (OSError, SuiteValidationError, subprocess.SubprocessError) as exc:
        print(f"performance_suite_failed:{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
