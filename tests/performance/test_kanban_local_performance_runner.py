from __future__ import annotations

import pytest

from scripts.performance.run_kanban_projection_local_diagnostic import (
    LOCAL_SCOPE,
    build_report,
    evaluate_baseline,
    exercise_events,
    validate_profile,
)


def _profile() -> dict:
    return {
        "schema": "ananta.kanban-model-dashboard.local-performance.v1",
        "scope": LOCAL_SCOPE,
        "task_count": 1000,
        "status_groups": [f"status-{index}" for index in range(10)],
        "view_groups": [f"view-{index}" for index in range(10)],
        "event_count": 1000,
        "duplicate_every": 10,
        "event_rate_min_per_second": 100,
        "samples": {
            "snapshot": 2,
            "move": 2,
            "filter_equivalent": 2,
        },
        "absolute_budgets": {
            "snapshot_p95_ms": 10,
            "move_p95_ms": 10,
            "filter_equivalent_p95_ms": 10,
            "peak_rss_mb": 100,
            "lost_events_max": 0,
            "event_rate_min_per_second": 100,
        },
        "max_baseline_regression_percent": 15.0,
    }


def _measurements() -> dict:
    return {
        "task_count": 1000,
        "status_group_count": 10,
        "view_group_count": 10,
        "snapshot_p50_ms": 1.0,
        "snapshot_p95_ms": 2.0,
        "move_p50_ms": 1.0,
        "move_p95_ms": 2.0,
        "filter_equivalent_p50_ms": 1.0,
        "filter_equivalent_p95_ms": 2.0,
        "peak_rss_mb": 25.0,
        "events_submitted": 1100,
        "events_unique_expected": 1000,
        "events_projected": 1000,
        "lost_events": 0,
        "deduped_events": 100,
        "event_rate_per_second": 1000.0,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_count", 999),
        ("status_groups", ["only-one"]),
        ("view_groups", ["only-one"]),
        ("event_rate_min_per_second", 99),
        ("max_baseline_regression_percent", 14),
    ],
)
def test_profile_rejects_reduced_diagnostic_scope(field, value) -> None:
    profile = _profile()
    profile[field] = value

    with pytest.raises(ValueError):
        validate_profile(profile)


def test_event_projection_reports_no_loss_and_exact_deduplication() -> None:
    result = exercise_events(event_count=1000, duplicate_every=10)

    assert result["events_submitted"] == 1100
    assert result["events_projected"] == 1000
    assert result["lost_events"] == 0
    assert result["deduped_events"] == 100
    assert result["event_rate_per_second"] >= 100


def test_baseline_rule_allows_at_most_fifteen_percent_regression() -> None:
    baseline = {"measurements": _measurements()}
    at_limit = _measurements()
    at_limit["snapshot_p95_ms"] = 2.3
    outside = dict(at_limit)
    outside["snapshot_p95_ms"] = 2.31

    accepted = evaluate_baseline(at_limit, baseline, 15.0)
    rejected = evaluate_baseline(outside, baseline, 15.0)

    assert accepted["within_regression_limit"] is True
    assert rejected["within_regression_limit"] is False


def test_report_cannot_be_promoted_to_release_evidence() -> None:
    profile = _profile()
    report = build_report(
        profile=profile,
        profile_bytes=b"deterministic-profile",
        measurements=_measurements(),
        baseline=None,
    )

    assert report["scope"] == "local_diagnostic_not_release_evidence"
    assert report["release_evidence"] is False
    assert report["formal_gate_eligible"] is False
    assert report["missing_surface_metrics"] == ["angular", "tui"]
    assert report["diagnostic_status"] == (
        "within_local_budgets_without_surface_evidence"
    )
