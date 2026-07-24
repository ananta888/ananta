from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from client_surfaces.operator_tui.region_index import build_region_index
from client_surfaces.operator_tui.renderer import render_operator_shell
from scripts.performance.run_tui_kanban_local_diagnostic import (
    LOCAL_SCOPE,
    build_board_snapshot,
    build_operator_state,
    build_report,
    evaluate_baseline,
    load_surface_payload,
    measure_event_loop_progress,
    validate_profile,
)


PROFILE_PATH = (
    Path(__file__).parents[2]
    / "config"
    / "test-profiles"
    / "kanban-model-dashboard"
    / "local-tui-performance.v1.json"
)


def _profile() -> dict:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def _measurements() -> dict:
    size = {
        "render_tick_p50_ms": 2.0,
        "render_tick_p95_ms": 4.0,
        "resize_region_rebuild_p50_ms": 3.0,
        "resize_region_rebuild_p95_ms": 5.0,
        "rendered_line_count": 24,
        "region_count": 22,
    }
    return {
        "card_count": 1000,
        "status_group_count": 10,
        "view_group_count": 10,
        "terminal_sizes": {
            "80x24": dict(size),
            "120x30": dict(size),
            "160x40": dict(size),
        },
        "peak_rss_mb": 32.0,
        "event_loop": {
            "iterations": 100,
            "target_rate_per_second": 100,
            "processed_events": 100,
            "lost_events": 0,
            "observed_rate_per_second": 101.0,
            "progress_ticks": 101,
            "progress_ratio": 1.0,
            "cooperative_tick_p50_ms": 3.0,
            "cooperative_tick_p95_ms": 5.0,
            "scheduling_lag_p50_ms": 1.0,
            "scheduling_lag_p95_ms": 2.0,
            "missed_deadlines": 0,
        },
    }


def test_profile_fixes_required_workload_and_terminal_sizes() -> None:
    profile = _profile()
    validate_profile(profile)

    assert profile["card_count"] == 1000
    assert len(profile["status_groups"]) == 10
    assert len(profile["view_groups"]) == 10
    assert profile["terminal_sizes"] == [
        {"columns": 80, "rows": 24},
        {"columns": 120, "rows": 30},
        {"columns": 160, "rows": 40},
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("card_count", 999),
        ("status_groups", ["one"]),
        ("view_groups", ["one"]),
        ("terminal_sizes", [{"columns": 80, "rows": 24}]),
        ("max_baseline_regression_percent", 14),
    ],
)
def test_profile_rejects_reduced_or_changed_scope(field, value) -> None:
    profile = _profile()
    profile[field] = value

    with pytest.raises(ValueError):
        validate_profile(profile)


def test_real_dashboard_surface_normalises_and_renders_1000_cards() -> None:
    profile = _profile()
    snapshot = build_board_snapshot(profile)
    payload = asyncio.run(load_surface_payload(profile))
    state = build_operator_state(payload)

    assert sum(len(column["tasks"]) for column in snapshot["columns"]) == 1000
    assert len({task["status"] for column in snapshot["columns"] for task in column["tasks"]}) == 10
    assert len(payload["columns"]) == 10
    assert len(payload["items"]) == 1000
    for size in profile["terminal_sizes"]:
        width = size["columns"]
        height = size["rows"]
        first = render_operator_shell(
            state,
            width=width,
            height=height,
            splash=None,
        )
        second = render_operator_shell(
            state,
            width=width,
            height=height,
            splash=None,
        )
        regions = build_region_index(
            state,
            width=width,
            height=height,
        )
        assert first == second
        assert len(first.splitlines()) <= height
        assert getattr(regions, "_regions", ())


def test_event_loop_progresses_during_real_surface_ticks() -> None:
    profile = _profile()
    payload = asyncio.run(load_surface_payload(profile))
    state = build_operator_state(payload)
    measured = asyncio.run(
        measure_event_loop_progress(
            state=state,
            terminal_sizes=profile["terminal_sizes"],
            iterations=12,
            target_rate_per_second=100,
        )
    )

    assert measured["progress_ticks"] >= 12
    assert measured["progress_ratio"] == 1.0
    assert measured["processed_events"] == 12
    assert measured["lost_events"] == 0
    assert measured["observed_rate_per_second"] > 0
    assert measured["cooperative_tick_p95_ms"] >= 0


def test_baseline_rule_is_exactly_fifteen_percent() -> None:
    prior = _measurements()
    baseline = {"measurements": prior}
    accepted = _measurements()
    accepted["terminal_sizes"]["80x24"]["render_tick_p95_ms"] = 4.6
    rejected = _measurements()
    rejected["terminal_sizes"]["80x24"]["render_tick_p95_ms"] = 4.61

    assert evaluate_baseline(
        accepted,
        baseline,
        15.0,
    )["within_regression_limit"] is True
    assert evaluate_baseline(
        rejected,
        baseline,
        15.0,
    )["within_regression_limit"] is False


def test_report_is_never_release_or_formal_gate_evidence() -> None:
    profile = _profile()
    report = build_report(
        profile=profile,
        profile_bytes=b"deterministic-tui-profile",
        measurements=_measurements(),
        baseline=None,
    )

    assert report["scope"] == LOCAL_SCOPE
    assert report["surface"] == "operator_tui"
    assert report["release_evidence"] is False
    assert report["formal_gate_eligible"] is False
    assert "angular" in report["missing_surface_metrics"]
    assert report["diagnostic_status"] == (
        "within_local_budgets_without_release_evidence"
    )
