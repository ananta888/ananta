"""TaskRetryPolicyService — bounded retry classification for artifact-first task execution.

Distinguishes parser failure, missing artifact, failed verification, and actual worker failure.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# Retry reason codes
REASON_PLANNER_LLM_PARSE_FAILED = "planner_llm_parse_failed"
REASON_ADVISORY_JSON_PARSE_FAILED = "advisory_json_parse_failed"
REASON_MISSING_REQUIRED_ARTIFACT = "missing_required_artifact"
REASON_MANIFEST_INVALID = "manifest_invalid"
REASON_VERIFICATION_FAILED = "verification_failed"
REASON_WORKER_EXECUTION_FAILED = "worker_execution_failed"
REASON_MAX_RETRIES_REACHED = "max_retries_reached"
REASON_UNKNOWN = "unknown"

# Retry classifications
CLASS_NON_RETRYABLE = "non_retryable"
CLASS_RETRYABLE = "retryable"
CLASS_NEEDS_REVIEW = "needs_review"
CLASS_IGNORED = "ignored"

DEFAULT_MAX_RETRIES = 3


class RetryClassification:
    def __init__(
        self,
        reason: str,
        classification: str,
        should_retry: bool,
        message: str = "",
    ) -> None:
        self.reason = reason
        self.classification = classification
        self.should_retry = should_retry
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "classification": self.classification,
            "should_retry": self.should_retry,
            "message": self.message,
        }


class TaskRetryPolicyService:
    def classify(
        self,
        *,
        reason: str,
        retry_count: int = 0,
        max_retries: int = DEFAULT_MAX_RETRIES,
        has_valid_artifacts: bool = False,
        deterministic_contract_exists: bool = True,
    ) -> RetryClassification:
        """Classify a retry reason and determine if task should be retried.

        Key rules:
        - planner_llm_parse_failed: non-retryable when deterministic contract exists
        - advisory_json_parse_failed: ignored when artifact completion succeeds
        - missing_required_artifact: retryable up to max_retries
        - verification_failed: policy-dependent (needs_review or retryable)
        - worker_execution_failed: retryable up to max_retries
        """
        exhausted = retry_count >= max_retries

        if reason == REASON_PLANNER_LLM_PARSE_FAILED:
            if deterministic_contract_exists:
                return RetryClassification(
                    reason=reason,
                    classification=CLASS_NON_RETRYABLE,
                    should_retry=False,
                    message="Deterministic contract exists; planner LLM parse failure is non-retryable.",
                )
            return RetryClassification(
                reason=reason,
                classification=CLASS_NEEDS_REVIEW,
                should_retry=False,
                message="No deterministic contract; needs manual review.",
            )

        if reason == REASON_ADVISORY_JSON_PARSE_FAILED:
            if has_valid_artifacts:
                return RetryClassification(
                    reason=reason,
                    classification=CLASS_IGNORED,
                    should_retry=False,
                    message="Advisory parse failed but artifact completion succeeded — ignored.",
                )
            return RetryClassification(
                reason=reason,
                classification=CLASS_NEEDS_REVIEW,
                should_retry=False,
                message="Advisory parse failed and no valid artifacts; needs review.",
            )

        if reason == REASON_MISSING_REQUIRED_ARTIFACT:
            if exhausted:
                return RetryClassification(
                    reason=reason,
                    classification=CLASS_NON_RETRYABLE,
                    should_retry=False,
                    message=f"Missing required artifact; max retries ({max_retries}) reached.",
                )
            return RetryClassification(
                reason=reason,
                classification=CLASS_RETRYABLE,
                should_retry=True,
                message=f"Missing required artifact; retry {retry_count + 1}/{max_retries}.",
            )

        if reason == REASON_MANIFEST_INVALID:
            if exhausted:
                return RetryClassification(
                    reason=reason,
                    classification=CLASS_NON_RETRYABLE,
                    should_retry=False,
                    message=f"Invalid manifest; max retries ({max_retries}) reached.",
                )
            return RetryClassification(
                reason=reason,
                classification=CLASS_RETRYABLE,
                should_retry=True,
                message=f"Invalid manifest; retry {retry_count + 1}/{max_retries}.",
            )

        if reason == REASON_VERIFICATION_FAILED:
            return RetryClassification(
                reason=reason,
                classification=CLASS_NEEDS_REVIEW,
                should_retry=False,
                message="Verification failed; needs manual review or policy-driven retry.",
            )

        if reason == REASON_WORKER_EXECUTION_FAILED:
            if exhausted:
                return RetryClassification(
                    reason=reason,
                    classification=CLASS_NON_RETRYABLE,
                    should_retry=False,
                    message=f"Worker execution failed; max retries ({max_retries}) reached.",
                )
            return RetryClassification(
                reason=reason,
                classification=CLASS_RETRYABLE,
                should_retry=True,
                message=f"Worker execution failed; retry {retry_count + 1}/{max_retries}.",
            )

        # Unknown reason — needs review
        return RetryClassification(
            reason=REASON_UNKNOWN,
            classification=CLASS_NEEDS_REVIEW,
            should_retry=False,
            message=f"Unknown reason: {reason!r}; needs manual review.",
        )

    def should_requeue(
        self,
        *,
        reason: str,
        retry_count: int = 0,
        max_retries: int = DEFAULT_MAX_RETRIES,
        has_valid_artifacts: bool = False,
        deterministic_contract_exists: bool = True,
    ) -> bool:
        """Return True only when a genuine retry is warranted — never for parse errors with valid artifacts."""
        cls = self.classify(
            reason=reason,
            retry_count=retry_count,
            max_retries=max_retries,
            has_valid_artifacts=has_valid_artifacts,
            deterministic_contract_exists=deterministic_contract_exists,
        )
        return cls.should_retry


task_retry_policy_service = TaskRetryPolicyService()


def get_task_retry_policy_service() -> TaskRetryPolicyService:
    return task_retry_policy_service
