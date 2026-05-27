from __future__ import annotations

from agent.game.artifact_guard import ArtifactGuard


def test_artifact_guard_verifies_successful_completion() -> None:
    decision = ArtifactGuard().verify_completion(
        task_id="ASG-005",
        evidence_refs=["artifact:1"],
        verification_passed=True,
        artifact_fresh=True,
    )
    assert decision.status == "verified"
    assert decision.points_awarded > 0


def test_artifact_guard_rejects_missing_evidence() -> None:
    decision = ArtifactGuard().verify_completion(
        task_id="ASG-005",
        evidence_refs=[],
        verification_passed=True,
    )
    assert decision.status == "open"
    assert decision.reason_code == "missing_evidence"


def test_artifact_guard_marks_failed_verification() -> None:
    decision = ArtifactGuard().verify_completion(
        task_id="ASG-005",
        evidence_refs=["artifact:1"],
        verification_passed=False,
    )
    assert decision.status == "failed"
    assert decision.reason_code == "verification_failed"


def test_artifact_guard_marks_stale_artifact() -> None:
    decision = ArtifactGuard().verify_completion(
        task_id="ASG-005",
        evidence_refs=["artifact:1"],
        verification_passed=True,
        artifact_fresh=False,
    )
    assert decision.status == "open"
    assert decision.reason_code == "stale_artifact"
