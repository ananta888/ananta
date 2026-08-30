from __future__ import annotations

import copy
import hashlib
import json

import pytest

from scripts.performance.kanban_baseline_approval_policy import (
    DEFAULT_POLICY,
    protected_candidate_sha256,
)
from scripts.performance.run_kanban_model_dashboard_performance_suite import (
    BASELINE_SCHEMA,
    ROOT,
    SuiteValidationError,
    build_baseline_candidate,
    build_gate_report,
    normalise_measurements,
    validate_profile,
)

PROFILE_PATH = (
    ROOT
    / "config"
    / "test-profiles"
    / "kanban-model-dashboard"
    / "formal-performance.v1.json"
)


def _profile() -> tuple[dict, str]:
    payload = PROFILE_PATH.read_bytes()
    return json.loads(payload), hashlib.sha256(payload).hexdigest()


def _source_hash(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def _reports(profile: dict) -> tuple[dict, dict, dict, dict]:
    backend = {
        "schema": profile["required_source_schemas"]["backend"],
        "scope": "local_diagnostic_not_release_evidence",
        "release_evidence": False,
        "formal_gate_eligible": False,
        "profile_sha256": _source_hash(profile["source_profiles"]["backend"]),
        "absolute_evaluation": {"within_budget": True},
        "measurements": {
            "task_count": 1000,
            "status_group_count": 10,
            "view_group_count": 10,
            "snapshot_p50_ms": 1.0,
            "snapshot_p95_ms": 2.0,
            "move_p50_ms": 3.0,
            "move_p95_ms": 4.0,
            "peak_rss_mb": 50.0,
            "event_rate_per_second": 200.0,
            "lost_events": 0,
            "deduped_events": 100,
        },
    }
    viewports = []
    for name, offset in (("desktop", 0.0), ("mobile", 10.0)):
        viewports.append(
            {
                "viewport": {"name": name},
                "samples": [
                    {"longTaskCount": 1},
                    {"longTaskCount": 2},
                    {"longTaskCount": 3},
                ],
                "summary": {
                    "initialRenderP50Ms": 100.0 + offset,
                    "initialRenderP95Ms": 120.0 + offset,
                    "filterP50Ms": 40.0 + offset,
                    "filterP95Ms": 50.0 + offset,
                    "longTaskTotalP95Ms": 20.0 + offset,
                    "longestTaskP95Ms": 10.0 + offset,
                    "retainedHeapP95Bytes": 10 * 1024 * 1024,
                    "longTaskApiAvailable": True,
                    "jsHeapAvailable": True,
                },
            }
        )
    angular = {
        "schema": profile["required_source_schemas"]["angular"],
        "evidence_classification": "local_diagnostic_not_release_evidence",
        "formal": False,
        "release_evidence": False,
        "dataset": {
            "cards": 1000,
            "canonical_columns": 4,
            "status_groups": 10,
            "view_groups": 10,
        },
        "methodology": {"measured_runs_per_viewport": 3},
        "viewports": viewports,
        "producer_runtime": {
            "node": {"version": "v24.18.0"},
            "playwright": {"version": "Version 1.60.0"},
            "browser": {"name": "chromium", "version": "140.0.0.0"},
        },
    }
    size = {
        "render_tick_p50_ms": 5.0,
        "render_tick_p95_ms": 8.0,
    }
    tui = {
        "schema": profile["required_source_schemas"]["tui"],
        "scope": "local_diagnostic_not_release_evidence",
        "release_evidence": False,
        "formal_gate_eligible": False,
        "profile_sha256": _source_hash(profile["source_profiles"]["tui"]),
        "absolute_evaluation": {"within_budget": True},
        "measurements": {
            "card_count": 1000,
            "status_group_count": 10,
            "view_group_count": 10,
            "terminal_sizes": {
                "80x24": dict(size),
                "120x30": dict(size),
                "160x40": dict(size),
            },
            "peak_rss_mb": 60.0,
            "event_loop": {
                "target_rate_per_second": 100,
                "observed_rate_per_second": 101.0,
                "progress_ratio": 1.0,
                "lost_events": 0,
            },
        },
    }
    resize = []
    for dimensions in profile["workload"]["terminal_sizes"]:
        resize.append(
            {
                **dimensions,
                "sample_count": 5,
                "redraw_latency_samples_ms": [1.0, 2.0, 3.0, 4.0, 5.0],
                "redraw_latency_p50_ms": 3.0,
                "redraw_latency_p95_ms": 5.0,
                "marker_present": True,
                "process_alive": True,
            }
        )
    pty = {
        "schema": profile["required_source_schemas"]["pty"],
        "scope": "local_diagnostic_not_release_evidence",
        "release_evidence": False,
        "formal_gate_eligible": False,
        "diagnostic_status": "passed_local_pty_resize",
        "card_count": 1000,
        "terminal_sizes": profile["workload"]["terminal_sizes"],
        "peak_rss_kib": 70 * 1024,
        "resize_measurements": resize,
    }
    return backend, angular, tui, pty


def _environment() -> dict:
    compatibility = {
        "os": {"system": "Linux", "release": "test", "machine": "x86_64"},
        "cpu": {"model": "test-cpu", "logical_count": 8},
        "memory": {"total_bytes": 16 * 1024**3},
        "python": {"implementation": "CPython", "version": "3.12.0"},
        "node": {"version": "v24.18.0"},
        "playwright": {"version": "Version 1.60.0"},
        "browser": {"name": "chromium", "version": "140.0.0.0"},
    }
    encoded = json.dumps(
        compatibility,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return {
        "host": {
            "hostname": "test-host",
            "os": compatibility["os"],
            "cpu": compatibility["cpu"],
            "memory": compatibility["memory"],
        },
        "runtimes": {
            "python": compatibility["python"],
            "node": compatibility["node"],
            "playwright": compatibility["playwright"],
        },
        "browser": compatibility["browser"],
        "compatibility": compatibility,
        "compatibility_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _commit() -> dict[str, str]:
    return {"sha": "a" * 40, "ref": "refs/heads/main", "source": "git_metadata_files"}


def _policy() -> tuple[dict, str]:
    payload = DEFAULT_POLICY.read_bytes()
    return json.loads(payload), hashlib.sha256(payload).hexdigest()


def _policy_approved(candidate: dict, *, approved_at: str) -> tuple[dict, dict, str]:
    policy, policy_hash = _policy()
    approved = copy.deepcopy(candidate)
    approved["approval_status"] = "approved"
    approved["approved_by"] = policy["approval_principal"]
    approved["approved_at"] = approved_at
    approved["approval"] = {
        "method": "hub_policy",
        "decision": "approved",
        "policy_id": policy["policy_id"],
        "policy_version": policy["policy_version"],
        "policy_sha256": policy_hash,
        "candidate_sha256": "b" * 64,
        "candidate_commit_sha": candidate["commit"]["sha"],
        "protected_payload_sha256": protected_candidate_sha256(approved),
    }
    return approved, policy, policy_hash


def _normalised() -> tuple[dict, str, dict, dict]:
    profile, profile_hash = _profile()
    backend, angular, tui, pty = _reports(profile)
    measurements, details = normalise_measurements(
        profile=profile,
        backend=backend,
        angular=angular,
        tui=tui,
        pty=pty,
    )
    return profile, profile_hash, measurements, details


def test_profile_fixes_formal_workload_and_fifteen_percent_rule() -> None:
    profile, _profile_hash = _profile()

    validate_profile(profile)

    assert profile["workload"]["task_count"] == 1000
    assert profile["workload"]["canonical_columns"] == [
        "todo",
        "in_progress",
        "blocked",
        "completed",
    ]
    assert len(profile["workload"]["view_groups"]) >= 10
    assert len(profile["workload"]["status_groups"]) >= 10
    assert profile["workload"]["events"]["target_rate_per_second"] == 100
    assert profile["baseline_comparison"]["max_regression_percent"] == 15.0


def test_normaliser_uses_all_four_real_source_contracts() -> None:
    _profile_data, _profile_hash, measurements, details = _normalised()

    assert measurements["backend.snapshot_p50_ms"] == 1.0
    assert measurements["angular.render_p95_ms"] == 130.0
    assert measurements["tui.render_p95_ms"] == 8.0
    assert measurements["tui.pty_resize_p50_ms"] == 3.0
    assert measurements["tui.pty_resize_p95_ms"] == 5.0
    assert measurements["memory.peak_rss_mb"] == 70.0
    assert measurements["events.lost"] == 0
    assert measurements["events.deduplicated"] == 100
    assert details["angular_retained_heap_p95_mb"] == 10.0


def test_normaliser_fails_closed_when_a_required_metric_is_missing() -> None:
    profile, _profile_hash = _profile()
    backend, angular, tui, pty = _reports(profile)
    del angular["viewports"][0]["summary"]["filterP95Ms"]

    with pytest.raises(SuiteValidationError, match="angular_filter_p95"):
        normalise_measurements(
            profile=profile,
            backend=backend,
            angular=angular,
            tui=tui,
            pty=pty,
        )


def test_candidate_is_never_self_approved() -> None:
    profile, profile_hash, measurements, details = _normalised()

    candidate = build_baseline_candidate(
        profile=profile,
        profile_sha256=profile_hash,
        measurements=measurements,
        details=details,
        sources={},
        environment=_environment(),
        commit=_commit(),
        created_at="2026-07-24T12:00:00+00:00",
    )

    assert candidate["schema"] == BASELINE_SCHEMA
    assert candidate["approval_status"] == "candidate_unapproved"
    assert candidate["approved_by"] is None
    assert candidate["approved_at"] is None
    assert candidate["absolute_evaluation"]["within_budget"] is True


def test_formal_gate_blocks_only_on_candidate_approval() -> None:
    profile, profile_hash, measurements, details = _normalised()
    environment = _environment()
    candidate = build_baseline_candidate(
        profile=profile,
        profile_sha256=profile_hash,
        measurements=measurements,
        details=details,
        sources={},
        environment=environment,
        commit=_commit(),
        created_at="2026-07-24T12:00:00+00:00",
    )

    report = build_gate_report(
        profile=profile,
        profile_sha256=profile_hash,
        measurements=measurements,
        details=details,
        sources={},
        environment=environment,
        commit=_commit(),
        baseline=candidate,
        evaluated_at="2026-07-24T12:01:00+00:00",
    )

    assert report["status"] == "blocked"
    assert report["release_evidence"] is False
    assert report["formal_gate_eligible"] is False
    assert report["absolute_evaluation"]["within_budget"] is True
    assert report["baseline_evaluation"]["within_regression_limit"] is True
    assert report["blockers"] == [{"code": "baseline_approval_required"}]


def test_approved_baseline_allows_exactly_fifteen_percent_regression() -> None:
    profile, profile_hash, measurements, details = _normalised()
    environment = _environment()
    candidate = build_baseline_candidate(
        profile=profile,
        profile_sha256=profile_hash,
        measurements=measurements,
        details=details,
        sources={},
        environment=environment,
        commit=_commit(),
        created_at="2026-07-24T12:00:00+00:00",
    )
    approved, policy, policy_hash = _policy_approved(
        candidate,
        approved_at="2026-07-24T12:30:00+00:00",
    )
    at_limit = dict(measurements)
    at_limit["backend.snapshot_p95_ms"] = (
        measurements["backend.snapshot_p95_ms"] * 1.15
    )

    accepted = build_gate_report(
        profile=profile,
        profile_sha256=profile_hash,
        measurements=at_limit,
        details=details,
        sources={},
        environment=environment,
        commit=_commit(),
        baseline=approved,
        approval_policy=policy,
        approval_policy_sha256=policy_hash,
    )
    outside = dict(at_limit)
    outside["backend.snapshot_p95_ms"] = (
        measurements["backend.snapshot_p95_ms"] * 1.1501
    )
    rejected = build_gate_report(
        profile=profile,
        profile_sha256=profile_hash,
        measurements=outside,
        details=details,
        sources={},
        environment=environment,
        commit=_commit(),
        baseline=approved,
        approval_policy=policy,
        approval_policy_sha256=policy_hash,
    )

    assert accepted["status"] == "passed"
    assert accepted["release_evidence"] is True
    assert accepted["formal_gate_eligible"] is True
    assert accepted["blockers"] == []
    assert rejected["status"] == "failed"
    assert rejected["blockers"] == [
        {
            "code": "baseline_regression_exceeded",
            "metrics": ["backend.snapshot_p95_ms"],
        }
    ]


def test_human_label_without_policy_attestation_is_rejected() -> None:
    profile, profile_hash, measurements, details = _normalised()
    environment = _environment()
    candidate = build_baseline_candidate(
        profile=profile,
        profile_sha256=profile_hash,
        measurements=measurements,
        details=details,
        sources={},
        environment=environment,
        commit=_commit(),
        created_at="2026-07-24T12:00:00+00:00",
    )
    candidate["approval_status"] = "approved"
    candidate["approved_by"] = "performance-review-board"
    candidate["approved_at"] = "2026-07-24T12:30:00+00:00"
    policy, policy_hash = _policy()

    report = build_gate_report(
        profile=profile,
        profile_sha256=profile_hash,
        measurements=measurements,
        details=details,
        sources={},
        environment=environment,
        commit=_commit(),
        baseline=candidate,
        approval_policy=policy,
        approval_policy_sha256=policy_hash,
    )

    assert report["status"] == "failed"
    assert report["blockers"] == [{"code": "baseline_approval_invalid"}]
