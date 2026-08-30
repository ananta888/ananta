from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.performance.kanban_baseline_approval_policy import (
    BASELINE_SCHEMA,
    DEFAULT_POLICY,
    BaselineApprovalError,
    GitState,
    promote_candidate,
    validate_policy_approval,
)

HEAD_SHA = "a" * 40
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
CANDIDATE_RELATIVE = Path(
    "artifacts/test-gates/"
    "kanban-model-dashboard-performance-baseline-candidate.v1.json"
)
PROFILE_RELATIVE = Path(
    "config/test-profiles/kanban-model-dashboard/formal-performance.v1.json"
)
POLICY_RELATIVE = Path(
    "config/test-profiles/kanban-model-dashboard/baseline-approval-policy.v1.json"
)


def _write(path: Path, value: object) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    return raw


def _fixture(root: Path) -> tuple[Path, Path, Path, dict, str]:
    policy = json.loads(DEFAULT_POLICY.read_text(encoding="utf-8"))
    policy_raw = _write(root / POLICY_RELATIVE, policy)
    profile = {
        "schema": "ananta.kanban-model-dashboard.performance-profile.v1",
        "profile_id": policy["profile_id"],
    }
    profile_raw = _write(root / PROFILE_RELATIVE, profile)
    sources = {}
    for source in ("backend", "angular", "tui", "pty"):
        relative = Path("artifacts") / f"{source}.json"
        raw = _write(root / relative, {"schema": f"test.{source}.v1"})
        sources[source] = {
            "path": relative.as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "schema": f"test.{source}.v1",
        }
    compatibility = {"runtime": "test", "machine": "x86_64"}
    candidate = {
        "schema": BASELINE_SCHEMA,
        "baseline_version": 1,
        "profile": {
            "id": profile["profile_id"],
            "schema": profile["schema"],
            "sha256": hashlib.sha256(profile_raw).hexdigest(),
        },
        "approval_status": "candidate_unapproved",
        "approved_by": None,
        "approved_at": None,
        "candidate_created_at": "2026-08-30T11:55:00+00:00",
        "commit": {"sha": HEAD_SHA, "ref": "refs/heads/main"},
        "environment": {
            "compatibility": compatibility,
            "compatibility_sha256": hashlib.sha256(
                json.dumps(
                    compatibility,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
        },
        "measurements": {"backend.snapshot_p95_ms": 12.0},
        "source_artifacts": sources,
        "absolute_evaluation": {
            "within_budget": True,
            "checks": {"backend.snapshot_p95_ms": {"passed": True}},
        },
        "candidate_status": "ready_for_policy_evaluation",
    }
    _write(root / CANDIDATE_RELATIVE, candidate)
    return (
        root / CANDIDATE_RELATIVE,
        root / PROFILE_RELATIVE,
        root / POLICY_RELATIVE,
        policy,
        hashlib.sha256(policy_raw).hexdigest(),
    )


def _candidate_only_state(_root: Path) -> GitState:
    return GitState(HEAD_SHA, (CANDIDATE_RELATIVE.as_posix(),))


def test_policy_promotes_candidate_without_human_input(tmp_path: Path) -> None:
    candidate, profile, policy_path, policy, policy_hash = _fixture(tmp_path)

    approved = promote_candidate(
        root=tmp_path,
        candidate_path=candidate,
        profile_path=profile,
        policy_path=policy_path,
        as_of=NOW,
        git_state_reader=_candidate_only_state,
    )

    assert approved["approval_status"] == "approved"
    assert approved["approved_by"] == policy["approval_principal"]
    assert approved["approval"]["method"] == "hub_policy"
    assert validate_policy_approval(
        baseline=approved,
        policy=policy,
        policy_sha256=policy_hash,
    )


def test_policy_rejects_unrelated_worktree_changes(tmp_path: Path) -> None:
    candidate, profile, policy_path, _policy, _policy_hash = _fixture(tmp_path)

    with pytest.raises(BaselineApprovalError, match="unapproved_changes"):
        promote_candidate(
            root=tmp_path,
            candidate_path=candidate,
            profile_path=profile,
            policy_path=policy_path,
            as_of=NOW,
            git_state_reader=lambda _root: GitState(
                HEAD_SHA,
                (CANDIDATE_RELATIVE.as_posix(), "agent/tampered.py"),
            ),
        )


def test_policy_rejects_tampered_source_artifact(tmp_path: Path) -> None:
    candidate, profile, policy_path, _policy, _policy_hash = _fixture(tmp_path)
    (tmp_path / "artifacts/backend.json").write_text("tampered", encoding="utf-8")

    with pytest.raises(BaselineApprovalError, match="source_artifact_mismatch:backend"):
        promote_candidate(
            root=tmp_path,
            candidate_path=candidate,
            profile_path=profile,
            policy_path=policy_path,
            as_of=NOW,
            git_state_reader=_candidate_only_state,
        )


def test_policy_rejects_stale_candidate(tmp_path: Path) -> None:
    candidate, profile, policy_path, _policy, _policy_hash = _fixture(tmp_path)
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    payload["candidate_created_at"] = "2026-08-28T11:55:00+00:00"
    _write(candidate, payload)

    with pytest.raises(BaselineApprovalError, match="candidate_freshness_invalid"):
        promote_candidate(
            root=tmp_path,
            candidate_path=candidate,
            profile_path=profile,
            policy_path=policy_path,
            as_of=NOW,
            git_state_reader=_candidate_only_state,
        )


def test_policy_attestation_detects_measurement_tampering(tmp_path: Path) -> None:
    candidate, profile, policy_path, policy, policy_hash = _fixture(tmp_path)
    approved = promote_candidate(
        root=tmp_path,
        candidate_path=candidate,
        profile_path=profile,
        policy_path=policy_path,
        as_of=NOW,
        git_state_reader=_candidate_only_state,
    )
    approved["measurements"]["backend.snapshot_p95_ms"] = 0.1

    assert not validate_policy_approval(
        baseline=approved,
        policy=policy,
        policy_sha256=policy_hash,
    )
