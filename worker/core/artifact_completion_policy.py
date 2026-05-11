"""ArtifactCompletionPolicy v1 — deterministic policy for artifact-based task completion."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


DECISION_COMPLETED = "completed"
DECISION_NEEDS_REVIEW = "needs_review"
DECISION_RETRYABLE_FAILED = "retryable_failed"
DECISION_FAILED = "failed"
DECISION_DENIED = "denied"
DECISION_DEGRADED = "degraded"


@dataclass
class CompletionDecision:
    decision: str
    reason_codes: list[str] = field(default_factory=list)
    advisory_parse_status: str | None = None
    manifest_id: str | None = None
    artifact_ids: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.decision in {DECISION_COMPLETED, DECISION_FAILED, DECISION_DENIED}

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason_codes": self.reason_codes,
            "advisory_parse_status": self.advisory_parse_status,
            "manifest_id": self.manifest_id,
            "artifact_ids": self.artifact_ids,
            "details": self.details,
        }


@dataclass
class ArtifactCompletionPolicy:
    policy_id: str
    task_id: str
    goal_id: str | None = None
    required_artifact_kinds: list[str] = field(default_factory=list)
    required_paths: list[str] = field(default_factory=list)
    optional_paths: list[str] = field(default_factory=list)
    verification_required: bool = False
    completion_on_valid_manifest: bool = True
    allow_synthesized_manifest: bool = False
    needs_review_conditions: list[str] = field(default_factory=list)
    retry_conditions: list[str] = field(default_factory=list)
    max_retries: int = 3

    def evaluate(
        self,
        *,
        validation_result: dict[str, Any],
        advisory_parse_result: dict[str, Any] | None = None,
        retry_count: int = 0,
        exit_code: int | None = None,
    ) -> CompletionDecision:
        """Evaluate whether artifacts satisfy completion policy.

        validation_result: output of ArtifactManifestService.validate_manifest()
        advisory_parse_result: output of parse_followup_analysis() — advisory only
        """
        manifest_valid = bool(validation_result.get("valid", validation_result.get("manifest_valid", False)))
        manifest_id = str(validation_result.get("manifest_id") or "")
        artifacts = list(validation_result.get("artifacts") or [])
        is_synthesized = bool(validation_result.get("synthesized", False))
        errors = list(validation_result.get("errors") or [])
        warnings = list(validation_result.get("warnings") or [])

        reason_codes: list[str] = []
        artifact_ids = [str(a.get("artifact_id") or "") for a in artifacts if a.get("artifact_id")]

        # Advisory parse status — recorded separately, never drives decision
        advisory_parse_status: str | None = None
        if advisory_parse_result:
            if advisory_parse_result.get("parse_error"):
                advisory_parse_status = "parse_failed"
                reason_codes.append("advisory_parse_failed_ignored")
            elif advisory_parse_result.get("task_complete") is not None:
                advisory_parse_status = "advisory_complete" if advisory_parse_result.get("task_complete") else "advisory_incomplete"

        # Synthesized manifest check
        if is_synthesized and not self.allow_synthesized_manifest:
            return CompletionDecision(
                decision=DECISION_NEEDS_REVIEW,
                reason_codes=["synthesized_manifest_not_allowed"],
                advisory_parse_status=advisory_parse_status,
                manifest_id=manifest_id,
                artifact_ids=artifact_ids,
                details={"errors": errors},
            )

        # Manifest not valid
        if not manifest_valid:
            if retry_count < self.max_retries:
                return CompletionDecision(
                    decision=DECISION_RETRYABLE_FAILED,
                    reason_codes=["manifest_invalid"] + errors[:5],
                    advisory_parse_status=advisory_parse_status,
                    manifest_id=manifest_id,
                    artifact_ids=artifact_ids,
                    details={"errors": errors, "retry_count": retry_count},
                )
            return CompletionDecision(
                decision=DECISION_FAILED,
                reason_codes=["manifest_invalid_max_retries_reached"] + errors[:5],
                advisory_parse_status=advisory_parse_status,
                manifest_id=manifest_id,
                artifact_ids=artifact_ids,
            )

        # Check required paths
        present_paths = {str(a.get("relative_path") or "") for a in artifacts if a.get("_exists")}
        missing_required = [p for p in self.required_paths if p not in present_paths]
        if missing_required:
            reason_codes.append("missing_required_artifact")
            if retry_count < self.max_retries:
                return CompletionDecision(
                    decision=DECISION_RETRYABLE_FAILED,
                    reason_codes=reason_codes + [f"missing:{p}" for p in missing_required],
                    advisory_parse_status=advisory_parse_status,
                    manifest_id=manifest_id,
                    artifact_ids=artifact_ids,
                    details={"missing_required": missing_required},
                )
            return CompletionDecision(
                decision=DECISION_FAILED,
                reason_codes=reason_codes + ["max_retries_reached"],
                advisory_parse_status=advisory_parse_status,
                manifest_id=manifest_id,
                artifact_ids=artifact_ids,
            )

        # Check verification when required
        if self.verification_required:
            unverified = [
                a.get("relative_path")
                for a in artifacts
                if a.get("required") and a.get("verification_status") not in ("verified",)
            ]
            if unverified:
                return CompletionDecision(
                    decision=DECISION_NEEDS_REVIEW,
                    reason_codes=["verification_required_but_not_verified"],
                    advisory_parse_status=advisory_parse_status,
                    manifest_id=manifest_id,
                    artifact_ids=artifact_ids,
                    details={"unverified": unverified},
                )

        # Non-zero exit code → needs_review but don't fail if artifacts passed
        if exit_code not in (None, 0):
            reason_codes.append("non_zero_exit_code")
            return CompletionDecision(
                decision=DECISION_NEEDS_REVIEW,
                reason_codes=reason_codes,
                advisory_parse_status=advisory_parse_status,
                manifest_id=manifest_id,
                artifact_ids=artifact_ids,
            )

        # All conditions met — completed
        if warnings:
            reason_codes.append("completed_with_warnings")
        return CompletionDecision(
            decision=DECISION_COMPLETED,
            reason_codes=reason_codes or ["artifact_manifest_verified"],
            advisory_parse_status=advisory_parse_status,
            manifest_id=manifest_id,
            artifact_ids=artifact_ids,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "artifact_completion_policy.v1",
            "policy_id": self.policy_id,
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "required_artifact_kinds": self.required_artifact_kinds,
            "required_paths": self.required_paths,
            "optional_paths": self.optional_paths,
            "verification_required": self.verification_required,
            "completion_on_valid_manifest": self.completion_on_valid_manifest,
            "allow_synthesized_manifest": self.allow_synthesized_manifest,
            "needs_review_conditions": self.needs_review_conditions,
            "retry_conditions": self.retry_conditions,
            "max_retries": self.max_retries,
        }

    @classmethod
    def for_task(
        cls,
        *,
        task_id: str,
        goal_id: str | None = None,
        required_paths: list[str] | None = None,
        verification_required: bool = False,
        allow_synthesized_manifest: bool = False,
    ) -> "ArtifactCompletionPolicy":
        return cls(
            policy_id=f"pol-{uuid.uuid4().hex[:12]}",
            task_id=task_id,
            goal_id=goal_id,
            required_paths=list(required_paths or []),
            verification_required=verification_required,
            allow_synthesized_manifest=allow_synthesized_manifest,
        )
