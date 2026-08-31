from datetime import UTC, datetime
from pathlib import Path

from scripts.run_sfu_broadcast_release_gate import DEFAULT_TODO, evaluate_release


def test_release_gate_default_todo_tracks_the_active_source() -> None:
    assert DEFAULT_TODO == (
        Path(__file__).resolve().parents[1]
        / "todos/active/todo.webrtc-sfu-broadcast-fanout.json"
    )
    assert DEFAULT_TODO.is_file()


def test_release_gate_never_activates_when_parent_is_no_go(tmp_path: Path) -> None:
    digest = "a" * 64
    todo = {
        "cross_track_prerequisites": ["parent.json:PARENT-1"],
        "tasks": [{
            "id": "SFB-GATE-011",
            "depends_on": ["parent.json:PARENT-1"],
        }],
    }
    gate_manifest = {
        "manifest_version": 1,
        "default_policy": {
            "minimum_manifest_version": 1,
            "max_age_seconds": 100,
            "max_future_skew_seconds": 1,
        },
        "gates": [],
        "release_policy": {
            "extra_required_gate_ids": [],
            "required_capacity_fields": [],
            "required_zero_counters": [],
        },
    }
    report = evaluate_release(
        gate_manifest=gate_manifest,
        evidence_manifest={
            "schema": "ananta.sfu-broadcast-evidence-manifest.v1",
            "manifest_version": 1,
            "entries": [],
        },
        todo=todo,
        parent={
            "decision": "no_go",
            "rollout_stage": "observe_only",
            "source_sha256": digest,
        },
        capacity={
            "schema": "ananta.sfu-broadcast-derived-capacity.v1",
            "status": "passed",
            "derived": True,
            "receiver_cap": 1,
        },
        rollback={
            "schema": "ananta.sfu-broadcast-game-day-gate.v1",
            "status": "passed",
            "summary": {
                "scenario_count": 1,
                "atomic_rollback_count": 1,
                "all_advanced_flags_disabled": True,
            },
        },
        risk_summary={
            "schema": "ananta.sfu-broadcast-risk-summary.v1",
            "kill_switch_verified": True,
            "open_findings": {},
            "known_residual_risks": [],
        },
        expected_source_sha256=digest,
        as_of=datetime(2026, 7, 22, tzinfo=UTC),
        artifact_root=tmp_path,
    )
    assert report["decision"] == "no_go"
    assert report["activation_allowed"] is False
    assert report["released_scopes"] == []


def test_release_gate_rejects_cross_track_dependency_drift(tmp_path: Path) -> None:
    digest = "b" * 64
    report = evaluate_release(
        gate_manifest={
            "manifest_version": 1,
            "default_policy": {
                "minimum_manifest_version": 1,
                "max_age_seconds": 100,
                "max_future_skew_seconds": 1,
            },
            "gates": [],
            "release_policy": {
                "extra_required_gate_ids": [],
                "required_capacity_fields": [],
                "required_zero_counters": [],
            },
        },
        evidence_manifest={
            "schema": "ananta.sfu-broadcast-evidence-manifest.v1",
            "manifest_version": 1,
            "entries": [],
        },
        todo={
            "cross_track_prerequisites": ["parent.json:PARENT-2"],
            "tasks": [{
                "id": "SFB-GATE-011",
                "depends_on": ["parent.json:PARENT-1"],
            }],
        },
        parent={
            "decision": "go",
            "rollout_stage": "bounded_pilot",
            "source_sha256": digest,
        },
        capacity={
            "schema": "ananta.sfu-broadcast-derived-capacity.v1",
            "status": "passed",
            "derived": True,
            "receiver_cap": 1,
        },
        rollback={
            "schema": "ananta.sfu-broadcast-game-day-gate.v1",
            "status": "passed",
            "summary": {
                "scenario_count": 1,
                "atomic_rollback_count": 1,
                "all_advanced_flags_disabled": True,
            },
        },
        risk_summary={
            "schema": "ananta.sfu-broadcast-risk-summary.v1",
            "kill_switch_verified": True,
            "open_findings": {},
            "known_residual_risks": [],
        },
        expected_source_sha256=digest,
        as_of=datetime(2026, 7, 22, tzinfo=UTC),
        artifact_root=tmp_path,
    )
    assert "release_cross_track_prerequisites_mismatch" in report["reason_codes"]
