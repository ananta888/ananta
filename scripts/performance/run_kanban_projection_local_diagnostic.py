#!/usr/bin/env python3
"""Run a reproducible local Kanban projection diagnostic.

This process deliberately cannot emit release evidence. Browser and terminal
UI metrics remain mandatory for any release claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE = (
    ROOT
    / "config"
    / "test-profiles"
    / "kanban-model-dashboard"
    / "local-performance.v1.json"
)
DEFAULT_OUTPUT = ROOT / "artifacts" / "kanban-local-performance-diagnostic.json"
LOCAL_SCOPE = "local_diagnostic_not_release_evidence"


@dataclass(slots=True)
class ProjectedTask:
    task_id: str
    status: str
    view_group: str
    revision: int = 1


class LocalProjection:
    """Deterministic equivalents of snapshot, move, and filtered projection."""

    def __init__(self, tasks: list[ProjectedTask]) -> None:
        self._tasks = {task.task_id: task for task in tasks}
        self._seen_commands: set[str] = set()

    def snapshot(self) -> dict[str, list[str]]:
        columns: dict[str, list[str]] = {}
        for task in self._tasks.values():
            columns.setdefault(task.status, []).append(task.task_id)
        for task_ids in columns.values():
            task_ids.sort()
        return columns

    def filter_equivalent(self, view_group: str) -> list[str]:
        return sorted(
            task.task_id
            for task in self._tasks.values()
            if task.view_group == view_group
        )

    def move(self, task_id: str, status: str, command_id: str) -> bool:
        if command_id in self._seen_commands:
            return False
        task = self._tasks[task_id]
        task.status = status
        task.revision += 1
        self._seen_commands.add(command_id)
        return True


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * quantile)))
    return ordered[index]


def resident_set_mb() -> float:
    """Return current RSS, avoiding inherited process high-water marks."""

    try:
        resident_pages = int(Path("/proc/self/statm").read_text().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE") / 1048576.0
    except (FileNotFoundError, IndexError, OSError, ValueError):
        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return (
            peak_rss / 1048576.0
            if sys.platform == "darwin"
            else peak_rss / 1024.0
        )


def validate_profile(profile: dict[str, Any]) -> None:
    problems: list[str] = []
    if profile.get("scope") != LOCAL_SCOPE:
        problems.append(f"scope must be {LOCAL_SCOPE}")
    if int(profile.get("task_count", 0)) < 1000:
        problems.append("task_count must be >= 1000")
    if len(profile.get("status_groups", [])) < 10:
        problems.append("status_groups must contain >= 10 entries")
    if len(profile.get("view_groups", [])) < 10:
        problems.append("view_groups must contain >= 10 entries")
    if float(profile.get("event_rate_min_per_second", 0)) < 100:
        problems.append("event_rate_min_per_second must be >= 100")
    if float(profile.get("max_baseline_regression_percent", -1)) != 15.0:
        problems.append("max_baseline_regression_percent must be exactly 15")
    if problems:
        raise ValueError("; ".join(problems))


def build_projection(profile: dict[str, Any]) -> LocalProjection:
    statuses = profile["status_groups"]
    views = profile["view_groups"]
    return LocalProjection(
        [
            ProjectedTask(
                task_id=f"hub-task-{index:04d}",
                status=statuses[index % len(statuses)],
                view_group=views[index % len(views)],
            )
            for index in range(profile["task_count"])
        ]
    )


def exercise_events(
    *,
    event_count: int,
    duplicate_every: int,
    repetitions: int = 1,
) -> dict[str, float | int]:
    events: list[str] = []
    expected = {f"event-{index:06d}" for index in range(event_count)}
    for index in range(event_count):
        event_id = f"event-{index:06d}"
        events.append(event_id)
        if duplicate_every > 0 and (index + 1) % duplicate_every == 0:
            events.append(event_id)

    started = time.perf_counter()
    projected: set[str] = set()
    for _ in range(max(1, repetitions)):
        projected = set()
        for event_id in events:
            projected.add(event_id)
    elapsed = max(time.perf_counter() - started, 1e-9)
    return {
        "events_submitted": len(events),
        "events_unique_expected": len(expected),
        "events_projected": len(projected),
        "lost_events": len(expected - projected),
        "deduped_events": len(events) - len(projected),
        "event_rate_per_second": (
            len(events) * max(1, repetitions) / elapsed
        ),
    }


def measure(profile: dict[str, Any]) -> dict[str, float | int]:
    projection = build_projection(profile)
    samples = profile["samples"]
    operations = profile.get("operations_per_sample", {})
    statuses = profile["status_groups"]
    views = profile["view_groups"]
    rss_samples = [resident_set_mb()]

    snapshot_ms: list[float] = []
    for _ in range(int(samples["snapshot"])):
        started = time.perf_counter()
        repeat = max(1, int(operations.get("snapshot", 1)))
        for _ in range(repeat):
            projection.snapshot()
        snapshot_ms.append(
            (time.perf_counter() - started) * 1000.0 / repeat
        )
    rss_samples.append(resident_set_mb())

    filter_ms: list[float] = []
    for index in range(int(samples["filter_equivalent"])):
        started = time.perf_counter()
        repeat = max(1, int(operations.get("filter_equivalent", 1)))
        for offset in range(repeat):
            projection.filter_equivalent(
                views[(index + offset) % len(views)]
            )
        filter_ms.append(
            (time.perf_counter() - started) * 1000.0 / repeat
        )
    rss_samples.append(resident_set_mb())

    move_ms: list[float] = []
    for index in range(int(samples["move"])):
        started = time.perf_counter()
        repeat = max(1, int(operations.get("move", 1)))
        for offset in range(repeat):
            operation = index * repeat + offset
            projection.move(
                f"hub-task-{operation % profile['task_count']:04d}",
                statuses[(operation + 1) % len(statuses)],
                f"move-command-{operation:08d}",
            )
        move_ms.append(
            (time.perf_counter() - started) * 1000.0 / repeat
        )
    rss_samples.append(resident_set_mb())

    event_metrics = exercise_events(
        event_count=int(profile["event_count"]),
        duplicate_every=int(profile["duplicate_every"]),
        repetitions=max(1, int(operations.get("events", 1))),
    )
    rss_samples.append(resident_set_mb())
    return {
        "task_count": int(profile["task_count"]),
        "status_group_count": len(statuses),
        "view_group_count": len(views),
        "snapshot_p50_ms": statistics.median(snapshot_ms),
        "snapshot_p95_ms": percentile(snapshot_ms, 0.95),
        "move_p50_ms": statistics.median(move_ms),
        "move_p95_ms": percentile(move_ms, 0.95),
        "filter_equivalent_p50_ms": statistics.median(filter_ms),
        "filter_equivalent_p95_ms": percentile(filter_ms, 0.95),
        "peak_rss_mb": max(rss_samples),
        **event_metrics,
    }


def evaluate_absolute(
    measurements: dict[str, float | int],
    budgets: dict[str, float | int],
) -> dict[str, Any]:
    checks = {
        "snapshot_p95_ms": measurements["snapshot_p95_ms"]
        <= budgets["snapshot_p95_ms"],
        "move_p95_ms": measurements["move_p95_ms"] <= budgets["move_p95_ms"],
        "filter_equivalent_p95_ms": measurements["filter_equivalent_p95_ms"]
        <= budgets["filter_equivalent_p95_ms"],
        "peak_rss_mb": measurements["peak_rss_mb"] <= budgets["peak_rss_mb"],
        "lost_events": measurements["lost_events"] <= budgets["lost_events_max"],
        "event_rate_per_second": measurements["event_rate_per_second"]
        >= budgets["event_rate_min_per_second"],
    }
    return {"within_budget": all(checks.values()), "checks": checks}


def evaluate_baseline(
    measurements: dict[str, float | int],
    baseline: dict[str, Any] | None,
    regression_percent: float,
) -> dict[str, Any]:
    if baseline is None:
        return {"evaluated": False, "within_regression_limit": None, "checks": {}}
    prior = baseline.get("measurements", {})
    upper_factor = 1.0 + regression_percent / 100.0
    lower_factor = 1.0 - regression_percent / 100.0
    lower_is_better = (
        "snapshot_p95_ms",
        "move_p95_ms",
        "filter_equivalent_p95_ms",
        "peak_rss_mb",
    )
    checks = {
        key: float(measurements[key]) <= float(prior[key]) * upper_factor
        for key in lower_is_better
    }
    checks["event_rate_per_second"] = float(measurements["event_rate_per_second"]) >= (
        float(prior["event_rate_per_second"]) * lower_factor
    )
    checks["lost_events"] = int(measurements["lost_events"]) <= int(
        prior["lost_events"]
    )
    return {
        "evaluated": True,
        "max_regression_percent": regression_percent,
        "within_regression_limit": all(checks.values()),
        "checks": checks,
    }


def build_report(
    *,
    profile: dict[str, Any],
    profile_bytes: bytes,
    measurements: dict[str, float | int],
    baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    absolute = evaluate_absolute(measurements, profile["absolute_budgets"])
    baseline_evaluation = evaluate_baseline(
        measurements,
        baseline,
        float(profile["max_baseline_regression_percent"]),
    )
    locally_within = absolute["within_budget"] and (
        not baseline_evaluation["evaluated"]
        or baseline_evaluation["within_regression_limit"]
    )
    return {
        "schema": "ananta.kanban-local-performance-diagnostic.v1",
        "scope": LOCAL_SCOPE,
        "release_evidence": False,
        "formal_gate_eligible": False,
        "missing_surface_metrics": ["angular", "tui"],
        "measurement_methods": {
            "peak_rss_mb": "maximum_sampled_current_process_rss",
            "latencies": "batched_operation_time_per_operation",
        },
        "diagnostic_status": (
            "within_local_budgets_without_surface_evidence"
            if locally_within
            else "outside_local_budgets"
        ),
        "profile_sha256": hashlib.sha256(profile_bytes).hexdigest(),
        "measurements": measurements,
        "absolute_evaluation": absolute,
        "baseline_evaluation": baseline_evaluation,
    }


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    profile_bytes = args.profile.read_bytes()
    profile = json.loads(profile_bytes)
    validate_profile(profile)
    baseline = json.loads(args.baseline.read_text()) if args.baseline else None
    measurements = measure(profile)
    report = build_report(
        profile=profile,
        profile_bytes=profile_bytes,
        measurements=measurements,
        baseline=baseline,
    )
    write_json_atomic(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return (
        0
        if report["absolute_evaluation"]["within_budget"]
        and (
            not report["baseline_evaluation"]["evaluated"]
            or report["baseline_evaluation"]["within_regression_limit"]
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
