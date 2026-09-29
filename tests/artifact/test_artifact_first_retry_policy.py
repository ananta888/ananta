"""AFH-T017: Retry policy matrix tests for artifact-first task execution."""
from __future__ import annotations

import pytest

from agent.services.task_retry_policy_service import (
    get_task_retry_policy_service,
    REASON_PLANNER_LLM_PARSE_FAILED,
    REASON_ADVISORY_JSON_PARSE_FAILED,
    REASON_MISSING_REQUIRED_ARTIFACT,
    REASON_MANIFEST_INVALID,
    REASON_VERIFICATION_FAILED,
    REASON_WORKER_EXECUTION_FAILED,
    CLASS_NON_RETRYABLE,
    CLASS_RETRYABLE,
    CLASS_NEEDS_REVIEW,
    CLASS_IGNORED,
)


svc = get_task_retry_policy_service()


class TestRetryMatrix:
    def test_planner_llm_parse_failed_non_retryable_with_deterministic_contract(self) -> None:
        cls = svc.classify(reason=REASON_PLANNER_LLM_PARSE_FAILED, deterministic_contract_exists=True)
        assert cls.should_retry is False
        assert cls.classification == CLASS_NON_RETRYABLE

    def test_planner_llm_parse_failed_needs_review_without_contract(self) -> None:
        cls = svc.classify(reason=REASON_PLANNER_LLM_PARSE_FAILED, deterministic_contract_exists=False)
        assert cls.should_retry is False
        assert cls.classification == CLASS_NEEDS_REVIEW

    def test_advisory_json_parse_failed_ignored_with_valid_artifacts(self) -> None:
        cls = svc.classify(reason=REASON_ADVISORY_JSON_PARSE_FAILED, has_valid_artifacts=True)
        assert cls.should_retry is False
        assert cls.classification == CLASS_IGNORED

    def test_advisory_json_parse_failed_needs_review_without_artifacts(self) -> None:
        cls = svc.classify(reason=REASON_ADVISORY_JSON_PARSE_FAILED, has_valid_artifacts=False)
        assert cls.should_retry is False
        assert cls.classification == CLASS_NEEDS_REVIEW

    def test_missing_required_artifact_retryable_within_limit(self) -> None:
        cls = svc.classify(reason=REASON_MISSING_REQUIRED_ARTIFACT, retry_count=1, max_retries=3)
        assert cls.should_retry is True
        assert cls.classification == CLASS_RETRYABLE

    def test_missing_required_artifact_non_retryable_at_max(self) -> None:
        cls = svc.classify(reason=REASON_MISSING_REQUIRED_ARTIFACT, retry_count=3, max_retries=3)
        assert cls.should_retry is False
        assert cls.classification == CLASS_NON_RETRYABLE

    def test_manifest_invalid_retryable_within_limit(self) -> None:
        cls = svc.classify(reason=REASON_MANIFEST_INVALID, retry_count=0, max_retries=3)
        assert cls.should_retry is True
        assert cls.classification == CLASS_RETRYABLE

    def test_manifest_invalid_non_retryable_at_max(self) -> None:
        cls = svc.classify(reason=REASON_MANIFEST_INVALID, retry_count=3, max_retries=3)
        assert cls.should_retry is False

    def test_verification_failed_needs_review(self) -> None:
        cls = svc.classify(reason=REASON_VERIFICATION_FAILED, retry_count=0)
        assert cls.should_retry is False
        assert cls.classification == CLASS_NEEDS_REVIEW

    def test_worker_execution_failed_retryable_within_limit(self) -> None:
        cls = svc.classify(reason=REASON_WORKER_EXECUTION_FAILED, retry_count=1, max_retries=3)
        assert cls.should_retry is True
        assert cls.classification == CLASS_RETRYABLE

    def test_worker_execution_failed_non_retryable_at_max(self) -> None:
        cls = svc.classify(reason=REASON_WORKER_EXECUTION_FAILED, retry_count=3, max_retries=3)
        assert cls.should_retry is False

    def test_should_requeue_false_for_advisory_parse_with_valid_artifacts(self) -> None:
        """Key regression: advisory parse failure with valid artifacts must never requeue."""
        result = svc.should_requeue(
            reason=REASON_ADVISORY_JSON_PARSE_FAILED,
            retry_count=0,
            has_valid_artifacts=True,
        )
        assert result is False

    def test_retry_classification_difference_from_missing_files(self) -> None:
        """Parser error and missing file must have different classifications."""
        parse_cls = svc.classify(reason=REASON_ADVISORY_JSON_PARSE_FAILED, has_valid_artifacts=True)
        missing_cls = svc.classify(reason=REASON_MISSING_REQUIRED_ARTIFACT, retry_count=0)
        assert parse_cls.classification != missing_cls.classification, (
            "Parser error with valid artifacts must NOT be classified same as missing files"
        )
