from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.performance.kanban_baseline_approval_policy import (
    DEFAULT_POLICY,
    protected_candidate_sha256,
)
from scripts.run_kanban_model_dashboard_evidence_preflight import (
    APPROVAL_POLICY_RELATIVE,
    APPROVED_BASELINE_RELATIVE,
    BASELINE_CANDIDATE_RELATIVE,
    FORMAL_PROFILE_RELATIVE,
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
    policy = json.loads(DEFAULT_POLICY.read_text(encoding="utf-8"))
    _write_json(root, APPROVAL_POLICY_RELATIVE, policy)
    profile = {
        "schema": "ananta.kanban-model-dashboard.performance-profile.v1",
        "profile_id": policy["profile_id"],
    }
    _write_json(root, FORMAL_PROFILE_RELATIVE, profile)
    profile_sha = hashlib.sha256(
        (root / FORMAL_PROFILE_RELATIVE).read_bytes()
    ).hexdigest()
    _write_json(
        root,
        BASELINE_CANDIDATE_RELATIVE,
        {
            "schema": "ananta.kanban-model-dashboard.performance-baseline.v1",
            "baseline_version": 1,
            "profile": {
                "id": profile["profile_id"],
                "schema": profile["schema"],
                "sha256": profile_sha,
            },
            "approval_status": "candidate_unapproved",
            "approved_by": None,
            "approved_at": None,
            "commit": {"sha": HEAD_SHA},
            "candidate_status": "ready_for_policy_evaluation",
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


def test_preflight_recognizes_policy_approved_baseline(
    tmp_path: Path,
) -> None:
    _write_current_performance_state(tmp_path)
    candidate_path = tmp_path / BASELINE_CANDIDATE_RELATIVE
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    policy_path = tmp_path / APPROVAL_POLICY_RELATIVE
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy_hash = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    approved = dict(candidate)
    approved["approval_status"] = "approved"
    approved["approved_by"] = policy["approval_principal"]
    approved["approved_at"] = "2026-07-24T12:00:00+00:00"
    approved["approval"] = {
        "method": "hub_policy",
        "decision": "approved",
        "policy_id": policy["policy_id"],
        "policy_version": policy["policy_version"],
        "policy_sha256": policy_hash,
        "candidate_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
        "candidate_commit_sha": HEAD_SHA,
        "protected_payload_sha256": protected_candidate_sha256(approved),
    }
    _write_json(
        tmp_path,
        APPROVED_BASELINE_RELATIVE,
        approved,
    )

    report = run_preflight(root=tmp_path, git_state_reader=_clean_git_state)

    assert report["status"] == "ready"
    assert report["reason_codes"] == []
    assert report["passed_evidence_eligible"] is True
    assert report["orchestrator"]["required_suite_count"] == 7
    assert report["orchestrator"]["configured_suite_count"] == 7
    assert report["orchestrator"]["performance_approved_baseline_only"] is True
    assert report["performance"]["approved_baseline"]["approval_method"] == (
        "hub_policy"
    )


def test_manual_approval_without_policy_attestation_is_rejected(
    tmp_path: Path,
) -> None:
    _write_current_performance_state(tmp_path)
    candidate = json.loads(
        (tmp_path / BASELINE_CANDIDATE_RELATIVE).read_text(encoding="utf-8")
    )
    candidate["approval_status"] = "approved"
    candidate["approved_by"] = "performance-review-board"
    candidate["approved_at"] = "2026-07-24T12:00:00+00:00"
    _write_json(tmp_path, APPROVED_BASELINE_RELATIVE, candidate)

    report = run_preflight(root=tmp_path, git_state_reader=_clean_git_state)

    assert report["status"] == "blocked"
    assert report["reason_codes"] == ["baseline_approval_required"]


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
