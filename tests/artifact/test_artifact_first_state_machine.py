"""AFH-T015: State machine regression tests — parser errors must not cause retry loops."""
from __future__ import annotations

from agent.services.planning_utils import parse_followup_analysis
from agent.services.task_retry_policy_service import (
    get_task_retry_policy_service,
    REASON_ADVISORY_JSON_PARSE_FAILED,
)


class TestStateMachineRetryLoopPrevention:
    def test_files_exist_but_json_invalid_does_not_requeue(self) -> None:
        """Regression: the exact observed failure scenario must not requeue."""
        # Worker produced valid files, but returned Markdown chat
        malformed_response = (
            "Great news! I successfully created the Fibonacci Flask application.\n"
            "Files created:\n- app.py\n- requirements.txt\n- README.md"
        )
        advisory = parse_followup_analysis(malformed_response)
        assert advisory["advisory"] is True
        assert advisory["parse_error"] is True

        # With valid artifacts (manifest_valid=True), must NOT requeue
        svc = get_task_retry_policy_service()
        result = svc.should_requeue(
            reason=REASON_ADVISORY_JSON_PARSE_FAILED,
            retry_count=0,
            has_valid_artifacts=True,
        )
        assert result is False, (
            "Regression: files exist + JSON invalid must NOT requeue task. "
            "This was the exact observed failure mode."
        )

    def test_parser_failure_increments_no_retry_counter(self) -> None:
        """Advisory parse failure must not increment retry counter."""
        svc = get_task_retry_policy_service()
        # Even at retry_count=0, advisory parse fail + valid artifacts = ignored
        cls = svc.classify(
            reason=REASON_ADVISORY_JSON_PARSE_FAILED,
            retry_count=0,
            has_valid_artifacts=True,
        )
        assert cls.should_retry is False
        assert cls.classification == "ignored"

    def test_retry_only_for_genuine_failures(self) -> None:
        """Only genuine failures (missing artifact, failed exec) cause retries."""
        from agent.services.task_retry_policy_service import REASON_MISSING_REQUIRED_ARTIFACT
        svc = get_task_retry_policy_service()
        genuine = svc.classify(reason=REASON_MISSING_REQUIRED_ARTIFACT, retry_count=0)
        assert genuine.should_retry is True

    def test_max_retry_guard_exists(self) -> None:
        """Terminal reason must be reached at max retries — no infinite loop."""
        from agent.services.task_retry_policy_service import REASON_MISSING_REQUIRED_ARTIFACT
        svc = get_task_retry_policy_service()
        cls = svc.classify(reason=REASON_MISSING_REQUIRED_ARTIFACT, retry_count=3, max_retries=3)
        assert cls.should_retry is False, "At max retries, must stop retrying."

    def test_parse_error_task_complete_none_not_authoritative(self) -> None:
        """task_complete=None from parse error must not drive completed state."""
        result = parse_followup_analysis("no json here at all")
        assert result["task_complete"] is None, (
            "task_complete must be None on parse error, not True. "
            "None explicitly means 'not authoritative'."
        )
        assert result["advisory"] is True
        assert result["parse_error"] is True

    def test_ambiguous_artifacts_go_to_needs_review_not_retry(self) -> None:
        """Partial artifacts reach needs_review, not infinite retry."""
        from worker.core.artifact_completion_policy import ArtifactCompletionPolicy, DECISION_NEEDS_REVIEW

        policy = ArtifactCompletionPolicy.for_task(
            task_id="t1",
            required_paths=["app.py"],
            allow_synthesized_manifest=False,
        )
        # Synthesized manifest without policy permission → needs_review, not retry
        collection = {
            "manifest_valid": True,
            "artifacts": [{"relative_path": "app.py", "_exists": True, "_hash_verified": True, "required": True}],
            "errors": [],
            "warnings": [],
            "synthesized": True,
        }
        decision = policy.evaluate(validation_result=collection, retry_count=0)
        assert decision.decision == DECISION_NEEDS_REVIEW, (
            "Synthesized manifest without permission must go to needs_review, not retry loop"
        )
