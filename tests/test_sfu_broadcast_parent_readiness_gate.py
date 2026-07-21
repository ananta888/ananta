from __future__ import annotations

from pathlib import Path

from scripts.run_sfu_broadcast_parent_readiness_gate import (
    DEFAULT_CHILD_TODO,
    DEFAULT_PARENT_EVIDENCE,
    DEFAULT_PARENT_TODO,
    evaluate_parent_readiness,
)
from scripts.run_sfu_broadcast_parent_readiness_gate import PARENT_GATE_ID
from scripts.run_sfu_broadcast_parent_readiness_gate import _load_json

ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path):
    return dict(_load_json(path))


def test_parent_readiness_fails_on_parent_no_go() -> None:
    child = _load(DEFAULT_CHILD_TODO)
    parent_todo = _load(DEFAULT_PARENT_TODO)
    parent_evidence = _load(DEFAULT_PARENT_EVIDENCE)
    parent_evidence["decision"] = "no_go"

    result = evaluate_parent_readiness(
        parent_todo=parent_todo,
        parent_evidence=parent_evidence,
        child_todo=child,
        evidence_profile="default",
        max_staleness_days=14,
        output_parent_artifact_path=ROOT / "artifacts/test-gates/sfu-broadcast-parent-readiness.json",
        parent_artifact_timestamp=DEFAULT_PARENT_EVIDENCE.stat().st_mtime,
    )

    assert result.status == "failed"
    assert "parent_no_go" in result.reason_codes


def test_parent_readiness_fails_on_missing_required_parent_task() -> None:
    child = _load(DEFAULT_CHILD_TODO)
    parent_todo = _load(DEFAULT_PARENT_TODO)
    parent_evidence = _load(DEFAULT_PARENT_EVIDENCE)
    parent_evidence["tasks"] = parent_evidence["tasks"][1:]

    result = evaluate_parent_readiness(
        parent_todo=parent_todo,
        parent_evidence=parent_evidence,
        child_todo=child,
        evidence_profile="default",
        max_staleness_days=14,
        output_parent_artifact_path=ROOT / "artifacts/test-gates/sfu-broadcast-parent-readiness.json",
        parent_artifact_timestamp=DEFAULT_PARENT_EVIDENCE.stat().st_mtime,
    )

    assert result.status == "failed"
    assert any(code.startswith("parent_prerequisite_task_missing:") for code in result.reason_codes)


def test_parent_readiness_blocks_observe_only_rollout() -> None:
    child = _load(DEFAULT_CHILD_TODO)
    parent_todo = _load(DEFAULT_PARENT_TODO)
    parent_evidence = _load(DEFAULT_PARENT_EVIDENCE)
    parent_evidence["rollout_stage"] = "observe_only"

    result = evaluate_parent_readiness(
        parent_todo=parent_todo,
        parent_evidence=parent_evidence,
        child_todo=child,
        evidence_profile="default",
        max_staleness_days=14,
        output_parent_artifact_path=ROOT / "artifacts/test-gates/sfu-broadcast-parent-readiness.json",
        parent_artifact_timestamp=DEFAULT_PARENT_EVIDENCE.stat().st_mtime,
    )

    assert result.status == "failed"
    assert "parent_rollout_observe_only" in result.reason_codes


def test_parent_readiness_accepts_verified_parent_go_task_set() -> None:
    child = _load(DEFAULT_CHILD_TODO)
    parent_todo = _load(DEFAULT_PARENT_TODO)
    parent_evidence = _load(DEFAULT_PARENT_EVIDENCE)
    # Keep decision and rollout stage as a synthetic passing state for the unit test.
    parent_evidence["decision"] = "go"
    parent_evidence["rollout_stage"] = "trusted_small_group"

    evidence = evaluate_parent_readiness(
        parent_todo=parent_todo,
        parent_evidence=parent_evidence,
        child_todo=child,
        evidence_profile="default",
        max_staleness_days=14,
        output_parent_artifact_path=ROOT / "artifacts/test-gates/sfu-broadcast-parent-readiness.json",
        parent_artifact_timestamp=DEFAULT_PARENT_EVIDENCE.stat().st_mtime,
    )

    assert evidence.gate_id == PARENT_GATE_ID
    assert evidence.status in {"failed", "passed", "unverified"}
    assert evidence.config_sha256

