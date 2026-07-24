#!/usr/bin/env python3
"""Measure the real Operator TUI dashboard surface with deterministic data.

The report is a local diagnostic only. It cannot supply Angular/browser
metrics and is intentionally excluded from formal release gates.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from client_surfaces.operator_tui.dashboard_surfaces import (  # noqa: E402
    DashboardFeatureFlags,
    DashboardSurfaceController,
)
from client_surfaces.operator_tui import sections as tui_sections  # noqa: E402
from client_surfaces.operator_tui.models import (  # noqa: E402
    FocusPane,
    OperatorState,
    PanelState,
)
from client_surfaces.operator_tui.region_index import (  # noqa: E402
    build_region_index,
)
from client_surfaces.operator_tui.renderer import render_operator_shell  # noqa: E402


LOCAL_SCOPE = "local_diagnostic_not_release_evidence"
DEFAULT_PROFILE = (
    ROOT
    / "config"
    / "test-profiles"
    / "kanban-model-dashboard"
    / "local-tui-performance.v1.json"
)
DEFAULT_OUTPUT = (
    ROOT / "artifacts" / "tui-kanban-local-performance-diagnostic.json"
)


class DeterministicKanbanPort:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self._snapshot = snapshot

    async def fetch_board(self) -> dict[str, Any]:
        return self._snapshot


@contextmanager
def enabled_kanban_section():
    original = tui_sections.SECTIONS
    if any(section.id == "kanban" for section in original):
        yield
        return
    optional = tui_sections.dashboard_sections(
        {"ANANTA_TUI_KANBAN_ENABLED": "1"}
    )
    tui_sections.SECTIONS = original + optional
    try:
        yield
    finally:
        tui_sections.SECTIONS = original


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * quantile)))
    return ordered[index]


def resident_set_mb() -> float:
    try:
        resident_pages = int(Path("/proc/self/statm").read_text().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE") / 1048576.0
    except (FileNotFoundError, IndexError, OSError, ValueError):
        return 0.0


def terminal_key(columns: int, rows: int) -> str:
    return f"{columns}x{rows}"


def validate_profile(profile: dict[str, Any]) -> None:
    problems: list[str] = []
    if profile.get("scope") != LOCAL_SCOPE:
        problems.append(f"scope must be {LOCAL_SCOPE}")
    if profile.get("surface") != "operator_tui":
        problems.append("surface must be operator_tui")
    if int(profile.get("card_count", 0)) != 1000:
        problems.append("card_count must be exactly 1000")
    if len(profile.get("status_groups", [])) != 10:
        problems.append("status_groups must contain exactly 10 entries")
    if len(profile.get("view_groups", [])) != 10:
        problems.append("view_groups must contain exactly 10 entries")
    sizes = {
        (int(item.get("columns", 0)), int(item.get("rows", 0)))
        for item in profile.get("terminal_sizes", [])
        if isinstance(item, dict)
    }
    if sizes != {(80, 24), (120, 30), (160, 40)}:
        problems.append("terminal_sizes must be 80x24, 120x30, and 160x40")
    if float(profile.get("max_baseline_regression_percent", -1)) != 15.0:
        problems.append("max_baseline_regression_percent must be exactly 15")
    if int(profile.get("event_loop_target_rate_per_second", 0)) != 100:
        problems.append("event_loop_target_rate_per_second must be exactly 100")
    if problems:
        raise ValueError("; ".join(problems))


def build_board_snapshot(profile: dict[str, Any]) -> dict[str, Any]:
    card_count = int(profile["card_count"])
    statuses = list(profile["status_groups"])
    views = list(profile["view_groups"])
    columns = [
        {
            "id": view,
            "title": f"View {index:02d}",
            "wip_limit": None,
            "tasks": [],
        }
        for index, view in enumerate(views)
    ]
    for index in range(card_count):
        group = index % len(views)
        columns[group]["tasks"].append(
            {
                "id": f"TUI-PERF-{index:04d}",
                "title": f"Deterministic card {index:04d}",
                "description": f"Status/view projection {group:02d}",
                "status": statuses[group],
                "priority": f"P{index % 4}",
                "assignee_id": f"worker-{index % 20:02d}",
                "labels": [views[group], statuses[group]],
                "blocked": index % 17 == 0,
                "dependencies": (
                    [f"TUI-PERF-{index - 1:04d}"] if index > 0 else []
                ),
                "revision": index + 1,
            }
        )
    return {
        "revision": "tui-local-diagnostic-r1",
        "columns": columns,
    }


async def load_surface_payload(profile: dict[str, Any]) -> dict[str, Any]:
    controller = DashboardSurfaceController(
        kanban_port=DeterministicKanbanPort(build_board_snapshot(profile)),
        flags=DashboardFeatureFlags(kanban=True, models=False),
    )
    result = await controller.load_kanban()
    return dict(result.payload)


def build_operator_state(payload: dict[str, Any]) -> OperatorState:
    return OperatorState(
        endpoint="",
        section_id="kanban",
        focus=FocusPane.CONTENT,
        selected_index=0,
        panel_states={"kanban": PanelState.HEALTHY},
        section_payloads={"kanban": payload},
        terminal_graphics={"no_3d": True},
    )


def _sample(
    operation,
    *,
    samples: int,
    operations_per_sample: int,
) -> list[float]:
    values: list[float] = []
    repeat = max(1, operations_per_sample)
    for sample in range(samples):
        started = time.perf_counter()
        for offset in range(repeat):
            operation(sample * repeat + offset)
        values.append((time.perf_counter() - started) * 1000.0 / repeat)
    return values


def _resize_region_rebuild(
    *,
    state: OperatorState,
    columns: int,
    rows: int,
    selected: int,
) -> tuple[int, int]:
    selected_state = replace(state, selected_index=selected)
    rendered = render_operator_shell(
        selected_state,
        width=columns,
        height=rows,
        splash=None,
    )
    regions = build_region_index(
        selected_state,
        width=columns,
        height=rows,
    )
    return len(rendered.splitlines()), len(getattr(regions, "_regions", ()))


async def _measure_event_loop_progress_enabled(
    *,
    state: OperatorState,
    terminal_sizes: list[dict[str, int]],
    iterations: int,
    target_rate_per_second: int = 100,
) -> dict[str, float | int]:
    progress_ticks = 0
    running = True

    async def heartbeat() -> None:
        nonlocal progress_ticks
        while running:
            progress_ticks += 1
            await asyncio.sleep(0)

    heartbeat_task = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    cooperative_ticks_ms: list[float] = []
    scheduling_lag_ms: list[float] = []
    interval = 1.0 / max(1, target_rate_per_second)
    loop = asyncio.get_running_loop()
    schedule_started = loop.time()
    processed = 0
    for index in range(iterations):
        deadline = schedule_started + index * interval
        await asyncio.sleep(max(0.0, deadline - loop.time()))
        scheduling_lag_ms.append(max(0.0, loop.time() - deadline) * 1000.0)
        size = terminal_sizes[index % len(terminal_sizes)]
        started = time.perf_counter()
        _resize_region_rebuild(
            state=state,
            columns=int(size["columns"]),
            rows=int(size["rows"]),
            selected=index % len(
                state.section_payloads["kanban"]["items"]
            ),
        )
        processed += 1
        await asyncio.sleep(0)
        cooperative_ticks_ms.append(
            (time.perf_counter() - started) * 1000.0
        )
    running = False
    await heartbeat_task
    elapsed = max(loop.time() - schedule_started, 1e-9)
    return {
        "iterations": iterations,
        "target_rate_per_second": target_rate_per_second,
        "processed_events": processed,
        "lost_events": iterations - processed,
        "observed_rate_per_second": processed / elapsed,
        "progress_ticks": progress_ticks,
        "progress_ratio": min(1.0, progress_ticks / max(1, iterations)),
        "cooperative_tick_p50_ms": percentile(cooperative_ticks_ms, 0.50),
        "cooperative_tick_p95_ms": percentile(cooperative_ticks_ms, 0.95),
        "scheduling_lag_p50_ms": percentile(scheduling_lag_ms, 0.50),
        "scheduling_lag_p95_ms": percentile(scheduling_lag_ms, 0.95),
        "missed_deadlines": sum(
            lag > interval * 1000.0 for lag in scheduling_lag_ms
        ),
    }


async def measure_event_loop_progress(
    *,
    state: OperatorState,
    terminal_sizes: list[dict[str, int]],
    iterations: int,
    target_rate_per_second: int = 100,
) -> dict[str, float | int]:
    with enabled_kanban_section():
        return await _measure_event_loop_progress_enabled(
            state=state,
            terminal_sizes=terminal_sizes,
            iterations=iterations,
            target_rate_per_second=target_rate_per_second,
        )


async def _measure_enabled(profile: dict[str, Any]) -> dict[str, Any]:
    payload = await load_surface_payload(profile)
    state = build_operator_state(payload)
    samples = profile["samples"]
    operations = profile["operations_per_sample"]
    rss_samples = [resident_set_mb()]
    by_size: dict[str, dict[str, float | int]] = {}

    for size in profile["terminal_sizes"]:
        columns = int(size["columns"])
        rows = int(size["rows"])
        _resize_region_rebuild(
            state=state,
            columns=columns,
            rows=rows,
            selected=0,
        )
        render_ms = _sample(
            lambda index: render_operator_shell(
                replace(
                    state,
                    selected_index=index % len(payload["items"]),
                ),
                width=columns,
                height=rows,
                splash=None,
            ),
            samples=int(samples["render_tick"]),
            operations_per_sample=int(operations["render_tick"]),
        )
        rebuild_ms = _sample(
            lambda index: _resize_region_rebuild(
                state=state,
                columns=columns,
                rows=rows,
                selected=index % len(payload["items"]),
            ),
            samples=int(samples["resize_region_rebuild"]),
            operations_per_sample=int(
                operations["resize_region_rebuild"]
            ),
        )
        lines, regions = _resize_region_rebuild(
            state=state,
            columns=columns,
            rows=rows,
            selected=0,
        )
        by_size[terminal_key(columns, rows)] = {
            "render_tick_p50_ms": percentile(render_ms, 0.50),
            "render_tick_p95_ms": percentile(render_ms, 0.95),
            "resize_region_rebuild_p50_ms": percentile(rebuild_ms, 0.50),
            "resize_region_rebuild_p95_ms": percentile(rebuild_ms, 0.95),
            "rendered_line_count": lines,
            "region_count": regions,
        }
        rss_samples.append(resident_set_mb())

    event_loop = await measure_event_loop_progress(
        state=state,
        terminal_sizes=profile["terminal_sizes"],
        iterations=int(samples["event_loop"]),
        target_rate_per_second=int(
            profile["event_loop_target_rate_per_second"]
        ),
    )
    rss_samples.append(resident_set_mb())
    return {
        "card_count": len(payload["items"]),
        "status_group_count": len(profile["status_groups"]),
        "view_group_count": len(payload["columns"]),
        "terminal_sizes": by_size,
        "peak_rss_mb": max(rss_samples),
        "event_loop": event_loop,
    }


async def measure(profile: dict[str, Any]) -> dict[str, Any]:
    with enabled_kanban_section():
        return await _measure_enabled(profile)


def evaluate_absolute(
    measurements: dict[str, Any],
    budgets: dict[str, Any],
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    for size, metrics in measurements["terminal_sizes"].items():
        checks[f"{size}.render_tick_p95_ms"] = (
            metrics["render_tick_p95_ms"]
            <= budgets["render_tick_p95_ms"]
        )
        checks[f"{size}.resize_region_rebuild_p95_ms"] = (
            metrics["resize_region_rebuild_p95_ms"]
            <= budgets["resize_region_rebuild_p95_ms"]
        )
    checks["peak_rss_mb"] = (
        measurements["peak_rss_mb"] <= budgets["peak_rss_mb"]
    )
    checks["event_loop.cooperative_tick_p95_ms"] = (
        measurements["event_loop"]["cooperative_tick_p95_ms"]
        <= budgets["event_loop_cooperative_tick_p95_ms"]
    )
    checks["event_loop.progress_ratio"] = (
        measurements["event_loop"]["progress_ratio"]
        >= budgets["event_loop_progress_ratio_min"]
    )
    checks["event_loop.scheduling_lag_p95_ms"] = (
        measurements["event_loop"]["scheduling_lag_p95_ms"]
        <= budgets["event_loop_scheduling_lag_p95_ms"]
    )
    return {"within_budget": all(checks.values()), "checks": checks}


def evaluate_baseline(
    measurements: dict[str, Any],
    baseline: dict[str, Any] | None,
    regression_percent: float,
) -> dict[str, Any]:
    if baseline is None:
        return {"evaluated": False, "within_regression_limit": None, "checks": {}}
    prior = baseline["measurements"]
    upper = 1.0 + regression_percent / 100.0
    lower = 1.0 - regression_percent / 100.0
    checks: dict[str, bool] = {}
    for size, metrics in measurements["terminal_sizes"].items():
        prior_size = prior["terminal_sizes"][size]
        for name in (
            "render_tick_p95_ms",
            "resize_region_rebuild_p95_ms",
        ):
            checks[f"{size}.{name}"] = (
                float(metrics[name]) <= float(prior_size[name]) * upper
            )
    checks["peak_rss_mb"] = (
        float(measurements["peak_rss_mb"])
        <= float(prior["peak_rss_mb"]) * upper
    )
    checks["event_loop.cooperative_tick_p95_ms"] = (
        float(measurements["event_loop"]["cooperative_tick_p95_ms"])
        <= float(prior["event_loop"]["cooperative_tick_p95_ms"]) * upper
    )
    checks["event_loop.progress_ratio"] = (
        float(measurements["event_loop"]["progress_ratio"])
        >= float(prior["event_loop"]["progress_ratio"]) * lower
    )
    checks["event_loop.scheduling_lag_p95_ms"] = (
        float(measurements["event_loop"]["scheduling_lag_p95_ms"])
        <= float(prior["event_loop"]["scheduling_lag_p95_ms"]) * upper
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
    measurements: dict[str, Any],
    baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    absolute = evaluate_absolute(
        measurements,
        profile["absolute_budgets"],
    )
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
        "schema": "ananta.tui-kanban-local-performance-diagnostic.v1",
        "scope": LOCAL_SCOPE,
        "surface": "operator_tui",
        "release_evidence": False,
        "formal_gate_eligible": False,
        "missing_surface_metrics": [
            "angular",
            "integrated_release_environment",
            "tui_pty_resize",
            "tui_live_event_transport",
        ],
        "diagnostic_status": (
            "within_local_budgets_without_release_evidence"
            if locally_within
            else "outside_local_budgets"
        ),
        "profile_sha256": hashlib.sha256(profile_bytes).hexdigest(),
        "measurement_methods": {
            "render_tick": "render_operator_shell_after_per_size_warmup",
            "resize_region_rebuild": (
                "render_operator_shell+build_region_index_after_per_size_warmup"
            ),
            "peak_rss_mb": "maximum_sampled_current_process_rss",
            "event_loop": "cooperative_dashboard_ticks_with_heartbeat_task",
        },
        "measurements": measurements,
        "absolute_evaluation": absolute,
        "baseline_evaluation": baseline_evaluation,
    }


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
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
    measurements = asyncio.run(measure(profile))
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
