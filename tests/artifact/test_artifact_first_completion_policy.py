"""AFH-T009/T014: ArtifactCompletionPolicy and TaskCompletionPolicyService tests."""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from agent.services.task_completion_policy_service import get_task_completion_policy_service
from worker.core.artifact_completion_policy import (
    ArtifactCompletionPolicy,
    DECISION_COMPLETED,
    DECISION_NEEDS_REVIEW,
    DECISION_RETRYABLE_FAILED,
    DECISION_FAILED,
)


def _make_valid_collection(required_paths: list[str] | None = None) -> dict:
    paths = required_paths or ["app.py", "requirements.txt", "README.md"]
    return {
        "manifest_valid": True,
        "artifacts": [
            {
                "artifact_id": f"art-{i}",
                "relative_path": p,
                "kind": "generated_file",
                "required": True,
                "_exists": True,
                "_hash_verified": True,
                "verification_status": "pending",
            }
            for i, p in enumerate(paths)
        ],
        "errors": [],
        "warnings": [],
        "synthesized": False,
        "manifest_id": "mfst-test",
    }


def _make_invalid_collection(error: str = "manifest_invalid") -> dict:
    return {
        "manifest_valid": False,
        "artifacts": [],
        "errors": [error],
        "warnings": [],
        "synthesized": False,
        "manifest_id": "",
    }


class TestArtifactCompletionPolicyDirect:
    def test_valid_artifacts_complete_task(self) -> None:
        policy = ArtifactCompletionPolicy.for_task(
            task_id="t1",
            required_paths=["app.py", "requirements.txt", "README.md"],
        )
        decision = policy.evaluate(validation_result=_make_valid_collection())
        assert decision.decision == DECISION_COMPLETED

    def test_missing_required_artifact_retryable(self) -> None:
        policy = ArtifactCompletionPolicy.for_task(
            task_id="t1",
            required_paths=["app.py", "missing.txt"],
        )
        collection = _make_valid_collection(["app.py"])  # missing.txt absent
        decision = policy.evaluate(validation_result=collection, retry_count=0)
        assert decision.decision == DECISION_RETRYABLE_FAILED
        assert "missing_required_artifact" in decision.reason_codes

    def test_missing_required_artifact_fails_at_max_retries(self) -> None:
        policy = ArtifactCompletionPolicy.for_task(task_id="t1", required_paths=["missing.txt"])
        collection = _make_valid_collection([])
        decision = policy.evaluate(validation_result=collection, retry_count=3)
        assert decision.decision == DECISION_FAILED

    def test_invalid_manifest_retryable(self) -> None:
        policy = ArtifactCompletionPolicy.for_task(task_id="t1")
        decision = policy.evaluate(validation_result=_make_invalid_collection(), retry_count=0)
        assert decision.decision == DECISION_RETRYABLE_FAILED

    def test_invalid_manifest_fails_at_max_retries(self) -> None:
        policy = ArtifactCompletionPolicy.for_task(task_id="t1")
        decision = policy.evaluate(validation_result=_make_invalid_collection(), retry_count=3)
        assert decision.decision == DECISION_FAILED

    def test_advisory_parse_failed_ignored_when_artifacts_pass(self) -> None:
        """Advisory parse failure must be ignored when artifacts are valid."""
        policy = ArtifactCompletionPolicy.for_task(task_id="t1")
        advisory = {"parse_error": True, "error_classification": "invalid_json", "task_complete": None, "advisory": True}
        decision = policy.evaluate(
            validation_result=_make_valid_collection(),
            advisory_parse_result=advisory,
        )
        assert decision.decision == DECISION_COMPLETED
        assert "advisory_parse_failed_ignored" in decision.reason_codes
        assert decision.advisory_parse_status == "parse_failed"

    def test_non_zero_exit_code_needs_review(self) -> None:
        policy = ArtifactCompletionPolicy.for_task(task_id="t1")
        decision = policy.evaluate(
            validation_result=_make_valid_collection(),
            exit_code=1,
        )
        assert decision.decision == DECISION_NEEDS_REVIEW
        assert "non_zero_exit_code" in decision.reason_codes

    def test_synthesized_manifest_needs_review_by_default(self) -> None:
        policy = ArtifactCompletionPolicy.for_task(task_id="t1", allow_synthesized_manifest=False)
        collection = {**_make_valid_collection(), "synthesized": True}
        decision = policy.evaluate(validation_result=collection)
        assert decision.decision == DECISION_NEEDS_REVIEW

    def test_synthesized_manifest_allowed_by_policy(self) -> None:
        policy = ArtifactCompletionPolicy.for_task(task_id="t1", allow_synthesized_manifest=True)
        collection = {**_make_valid_collection(), "synthesized": True}
        decision = policy.evaluate(validation_result=collection)
        assert decision.decision == DECISION_COMPLETED

    def test_fibonacci_flask_policy(self) -> None:
        """Fibonacci Flask project specific policy requirement."""
        policy = ArtifactCompletionPolicy.for_task(
            task_id="task-fibonacci",
            goal_id="goal-fibonacci",
            required_paths=["app.py", "requirements.txt", "README.md"],
        )
        decision = policy.evaluate(
            validation_result=_make_valid_collection(["app.py", "requirements.txt", "README.md"]),
        )
        assert decision.decision == DECISION_COMPLETED


class TestTaskCompletionPolicyService:
    def test_service_evaluates_correctly(self) -> None:
        svc = get_task_completion_policy_service()
        decision = svc.evaluate(
            task_id="t1",
            collection_result=_make_valid_collection(),
            expected_paths=["app.py", "requirements.txt", "README.md"],
        )
        assert decision.decision == DECISION_COMPLETED

    def test_service_to_status_mapping(self) -> None:
        svc = get_task_completion_policy_service()
        from worker.core.artifact_completion_policy import CompletionDecision
        assert svc.to_status(CompletionDecision(DECISION_COMPLETED)) == "completed"
        assert svc.to_status(CompletionDecision(DECISION_NEEDS_REVIEW)) == "needs_review"
        assert svc.to_status(CompletionDecision(DECISION_RETRYABLE_FAILED)) == "queued"
        assert svc.to_status(CompletionDecision(DECISION_FAILED)) == "failed"

    def test_advisory_parse_failure_logged_but_not_authoritative(self) -> None:
        svc = get_task_completion_policy_service()
        advisory = {"parse_error": True, "error_classification": "missing_json", "task_complete": None, "advisory": True}
        decision = svc.evaluate(
            task_id="t1",
            collection_result=_make_valid_collection(),
            advisory_parse_result=advisory,
        )
        # Decision is still completed — advisory parse failure doesn't change it
        assert decision.decision == DECISION_COMPLETED
        assert "advisory_parse_failed_ignored" in decision.reason_codes
