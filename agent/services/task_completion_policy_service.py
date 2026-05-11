"""TaskCompletionPolicyService — artifact-first, centralized task completion decisions.

Malformed model JSON is advisory only. Completion is based on artifacts, manifests,
verification evidence and exit status.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from worker.core.artifact_completion_policy import (
    ArtifactCompletionPolicy,
    CompletionDecision,
    DECISION_COMPLETED,
    DECISION_FAILED,
    DECISION_NEEDS_REVIEW,
    DECISION_RETRYABLE_FAILED,
    DECISION_DEGRADED,
)

log = logging.getLogger(__name__)


class TaskCompletionPolicyService:
    """Evaluate whether a task is complete based on artifact evidence, not model chat."""

    def evaluate(
        self,
        *,
        task_id: str,
        goal_id: str | None = None,
        collection_result: dict[str, Any],
        advisory_parse_result: dict[str, Any] | None = None,
        exit_code: int | None = None,
        retry_count: int = 0,
        policy: ArtifactCompletionPolicy | None = None,
        expected_paths: list[str] | None = None,
        verification_required: bool = False,
        allow_synthesized_manifest: bool = False,
    ) -> CompletionDecision:
        """Make artifact-first completion decision.

        advisory_parse_result: output of parse_followup_analysis() — stored for audit, never authoritative.
        collection_result: output of WorkerOutputCollectorService.collect().
        """
        if policy is None:
            policy = ArtifactCompletionPolicy.for_task(
                task_id=task_id,
                goal_id=goal_id,
                required_paths=list(expected_paths or []),
                verification_required=verification_required,
                allow_synthesized_manifest=allow_synthesized_manifest,
            )

        decision = policy.evaluate(
            validation_result=collection_result,
            advisory_parse_result=advisory_parse_result,
            retry_count=retry_count,
            exit_code=exit_code,
        )

        if advisory_parse_result and advisory_parse_result.get("parse_error"):
            log.info(
                "TaskCompletionPolicyService: advisory JSON parse failed for task %s — "
                "ignored; completion based on artifacts only. reason_code=advisory_parse_failed_ignored",
                task_id,
            )

        log.info(
            "TaskCompletionPolicyService: task=%s decision=%s reason_codes=%s",
            task_id, decision.decision, decision.reason_codes,
        )
        return decision

    def to_status(self, decision: CompletionDecision) -> str:
        """Map completion decision to task status string."""
        mapping = {
            DECISION_COMPLETED: "completed",
            DECISION_NEEDS_REVIEW: "needs_review",
            DECISION_RETRYABLE_FAILED: "queued",
            DECISION_FAILED: "failed",
            DECISION_DEGRADED: "degraded",
            "denied": "denied",
        }
        return mapping.get(decision.decision, "failed")


task_completion_policy_service = TaskCompletionPolicyService()


def get_task_completion_policy_service() -> TaskCompletionPolicyService:
    return task_completion_policy_service
