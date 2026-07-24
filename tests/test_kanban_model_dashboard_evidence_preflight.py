from __future__ import annotations

import json
from pathlib import Path

from scripts.run_kanban_model_dashboard_evidence_preflight import (
    APPROVED_BASELINE_RELATIVE,
    BASELINE_CANDIDATE_RELATIVE,
    PERFORMANCE_GATE_RELATIVE,
    run_preflight,
)


HEAD_SHA = "a" * 40


def _write_json(root: Path, relative: Path, value: object) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _dirty_git_state(_root: Path) -> tuple[str, bool, None]:
    return HEAD_SHA, True, None


def _clean_git_state(_root: Path) -> tuple[str, bool, None]:
    return HEAD_SHA, False, None


def _write_current_performance_state(root: Path) -> None:
    _write_json(
        root,
        BASELINE_CANDIDATE_RELATIVE,
        {
            "schema": "ananta.kanban-model-dashboard.performance-baseline.v1",
            "approval_status": "candidate_unapproved",
            "status": "ready_for_organizational_review",
        },
    )
    _write_json(
        root,
        PERFORMANCE_GATE_RELATIVE,
        {
            "schema": "ananta.kanban-model-dashboard.performance-gate.v1",
            "suite_id": "kanban-model-dashboard.performance.v1",
            "status": "blocked",
            "release_evidence": False,
            "formal_gate_eligible": False,
            "blockers": [{"code": "baseline_approval_required"}],
        },
    )


def test_preflight_reports_exact_current_boundaries_without_writing(
    tmp_path: Path,
) -> None:
    _write_current_performance_state(tmp_path)

    report = run_preflight(root=tmp_path, git_state_reader=_dirty_git_state)

    assert report["status"] == "blocked"
    assert report["read_only"] is True
    assert report["commands_executed"] is False
    assert report["evidence_written"] is False
    assert report["reason_codes"] == [
        "uncommitted_candidate",
        "baseline_approval_required",
    ]
    assert report["performance"]["baseline_candidate"]["approval_status"] == (
        "candidate_unapproved"
    )
    assert report["performance"]["last_gate"]["blocked_contract_valid"] is True
    assert not (tmp_path / "artifacts/e2e/kanban-model-dashboard").exists()


def test_preflight_recognizes_only_an_explicitly_approved_baseline(
    tmp_path: Path,
) -> None:
    _write_current_performance_state(tmp_path)
    _write_json(
        tmp_path,
        APPROVED_BASELINE_RELATIVE,
        {
            "schema": "ananta.kanban-model-dashboard.performance-baseline.v1",
            "approval_status": "approved",
            "approved_by": "performance-review-board",
            "approved_at": "2026-07-24T12:00:00+00:00",
        },
    )

    report = run_preflight(root=tmp_path, git_state_reader=_clean_git_state)

    assert report["status"] == "ready"
    assert report["reason_codes"] == []
    assert report["passed_evidence_eligible"] is True
    assert report["orchestrator"]["required_suite_count"] == 7
    assert report["orchestrator"]["configured_suite_count"] == 7
    assert report["orchestrator"]["performance_approved_baseline_only"] is True


def test_candidate_unapproved_cannot_replace_the_approved_baseline(
    tmp_path: Path,
) -> None:
    _write_current_performance_state(tmp_path)
    candidate = json.loads(
        (tmp_path / BASELINE_CANDIDATE_RELATIVE).read_text(encoding="utf-8")
    )
    _write_json(tmp_path, APPROVED_BASELINE_RELATIVE, candidate)

    report = run_preflight(root=tmp_path, git_state_reader=_clean_git_state)

    assert report["status"] == "blocked"
    assert report["reason_codes"] == ["baseline_approval_required"]
    assert report["performance"]["approved_baseline"]["present_and_approved"] is False
