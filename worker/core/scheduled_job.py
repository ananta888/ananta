"""Scheduled worker job contract.

EW-T054: ScheduledJobContract — Hub-owned, capability_grant, context_policy,
          max_runtime, approval_mode, delivery_target. Worker never owns the schedule.
EW-T055: HeadlessApprovalPolicy — confirm_required without pre-approved ref → blocked.
EW-T056: JobRunArtifact — status, started_at, ended_at, artifacts, trace_bundle_ref,
          warnings, retry recommendation.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enums ─────────────────────────────────────────────────────────────────────

class ApprovalMode(str, Enum):
    pre_approved = "pre_approved"     # approval_refs provided with the envelope
    confirm_required = "confirm_required"  # worker must pause and await hub approval
    auto_deny = "auto_deny"           # no approval ever granted; blocks all sensitive ops


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    success = "success"
    failure = "failure"
    blocked = "blocked"       # blocked by approval policy
    cancelled = "cancelled"
    timeout = "timeout"


class DeliveryTarget(str, Enum):
    hub = "hub"               # result delivered back to Hub
    artifact_store = "artifact_store"  # result written to artifact store only
    callback_url = "callback_url"      # HTTP callback


# ── ScheduledJobContract ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class ContextPolicy:
    """Scope of context available to the scheduled job."""
    allowed_refs: list[str] = field(default_factory=list)
    max_tokens: int = 8192
    cloud_allowed: bool = False
    include_session_history: bool = False

    def __post_init__(self):
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")


@dataclass(frozen=True)
class ScheduledJobContract:
    """Hub-owned contract for a recurring or one-shot worker job. EW-T054.

    Worker never stores or modifies the schedule — it only executes within
    the boundaries this contract defines.
    """
    job_id: str
    task_template: str           # task description template
    capability_grant_ids: list[str]  # capability classes granted for this job
    schedule_cron: str           # cron expression owned by Hub, informational only
    context_policy: ContextPolicy
    approval_mode: ApprovalMode
    max_runtime_seconds: int     # hard ceiling; worker must abort when exceeded
    delivery_target: DeliveryTarget
    pre_approved_ref_ids: list[str] = field(default_factory=list)
    delivery_url: str = ""       # required when target == callback_url
    retry_limit: int = 0
    created_by: str = ""
    created_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if self.max_runtime_seconds <= 0:
            raise ValueError("max_runtime_seconds must be positive")
        if self.delivery_target == DeliveryTarget.callback_url and not self.delivery_url:
            raise ValueError("delivery_url required when target is callback_url")


# ── HeadlessApprovalPolicy ────────────────────────────────────────────────────

@dataclass
class ApprovalCheckResult:
    allowed: bool
    reason_code: str
    detail: str = ""


class HeadlessApprovalPolicy:
    """Enforces approval requirements for headless (unattended) job execution. EW-T055.

    confirm_required without a matching pre-approved ref → blocked.
    auto_deny → always blocked for sensitive operations.
    pre_approved → allowed only when ref_id is in the pre_approved_ref_ids list.
    """

    def check(
        self,
        contract: ScheduledJobContract,
        *,
        operation: str,
        ref_id: str = "",
    ) -> ApprovalCheckResult:
        """Check whether a sensitive operation is approved for this job run."""
        mode = contract.approval_mode

        if mode == ApprovalMode.auto_deny:
            return ApprovalCheckResult(
                False,
                "approval_auto_denied",
                f"job {contract.job_id!r} approval_mode is auto_deny",
            )

        if mode == ApprovalMode.confirm_required:
            # Headless: cannot wait for interactive confirmation.
            # Allowed only if a pre-approved ref matching this operation exists.
            if not ref_id or ref_id not in contract.pre_approved_ref_ids:
                return ApprovalCheckResult(
                    False,
                    "approval_missing",
                    f"confirm_required for {operation!r} but no matching pre-approved ref",
                )
            return ApprovalCheckResult(True, "approval_ok")

        if mode == ApprovalMode.pre_approved:
            if not contract.pre_approved_ref_ids:
                return ApprovalCheckResult(
                    False,
                    "approval_missing",
                    f"pre_approved mode but no ref_ids present in contract",
                )
            if ref_id and ref_id not in contract.pre_approved_ref_ids:
                return ApprovalCheckResult(
                    False,
                    "approval_ref_not_found",
                    f"ref_id {ref_id!r} not in pre_approved_ref_ids",
                )
            return ApprovalCheckResult(True, "approval_ok")

        return ApprovalCheckResult(False, "unknown_approval_mode")

    def check_can_run_headless(
        self,
        contract: ScheduledJobContract,
        required_operations: list[str],
    ) -> ApprovalCheckResult:
        """Pre-flight check: can this job run headless at all?

        Returns blocked if any required sensitive operation cannot be approved.
        """
        if contract.approval_mode == ApprovalMode.auto_deny and required_operations:
            return ApprovalCheckResult(
                False,
                "approval_auto_denied",
                f"job {contract.job_id!r} has auto_deny mode — cannot run sensitive ops headless",
            )
        if contract.approval_mode == ApprovalMode.confirm_required:
            for op in required_operations:
                result = self.check(contract, operation=op, ref_id="")
                if not result.allowed:
                    return result
        return ApprovalCheckResult(True, "headless_ok")


# ── JobRunArtifact ────────────────────────────────────────────────────────────

@dataclass
class JobRunArtifact:
    """Structured result of a scheduled job execution. EW-T056."""
    artifact_id: str
    job_id: str
    task_id: str
    status: JobStatus
    started_at: float
    ended_at: float
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    trace_bundle_ref: str = ""
    warnings: list[str] = field(default_factory=list)
    error_detail: str = ""
    retry_recommended: bool = False
    retry_count: int = 0

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.ended_at - self.started_at)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "job_run_artifact",
            "artifact_id": self.artifact_id,
            "job_id": self.job_id,
            "task_id": self.task_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": self.duration_seconds,
            "artifact_count": len(self.artifacts),
            "artifact_refs": [
                a.get("artifact_id", a.get("id", "")) for a in self.artifacts
            ],
            "trace_bundle_ref": self.trace_bundle_ref,
            "warnings": self.warnings,
            "error_detail": self.error_detail,
            "retry_recommended": self.retry_recommended,
            "retry_count": self.retry_count,
        }


class JobRunArtifactBuilder:
    """Builds JobRunArtifact; caller provides start time and fills in results."""

    def __init__(self, job_id: str, task_id: str) -> None:
        self._artifact_id = f"job-{uuid.uuid4().hex[:12]}"
        self._job_id = job_id
        self._task_id = task_id
        self._started_at = time.time()
        self._artifacts: list[dict[str, Any]] = []
        self._warnings: list[str] = []
        self._trace_ref = ""
        self._error = ""
        self._retry_count = 0

    def add_artifact(self, artifact: dict[str, Any]) -> "JobRunArtifactBuilder":
        self._artifacts.append(artifact)
        return self

    def add_warning(self, warning: str) -> "JobRunArtifactBuilder":
        self._warnings.append(warning)
        return self

    def set_trace_ref(self, ref: str) -> "JobRunArtifactBuilder":
        self._trace_ref = ref
        return self

    def set_error(self, detail: str) -> "JobRunArtifactBuilder":
        self._error = detail
        return self

    def set_retry_count(self, count: int) -> "JobRunArtifactBuilder":
        self._retry_count = count
        return self

    def finish(
        self,
        status: JobStatus,
        *,
        contract: ScheduledJobContract | None = None,
    ) -> JobRunArtifact:
        ended_at = time.time()
        retry_recommended = (
            status in (JobStatus.failure, JobStatus.timeout)
            and contract is not None
            and self._retry_count < contract.retry_limit
        )
        return JobRunArtifact(
            artifact_id=self._artifact_id,
            job_id=self._job_id,
            task_id=self._task_id,
            status=status,
            started_at=self._started_at,
            ended_at=ended_at,
            artifacts=list(self._artifacts),
            trace_bundle_ref=self._trace_ref,
            warnings=list(self._warnings),
            error_detail=self._error,
            retry_recommended=retry_recommended,
            retry_count=self._retry_count,
        )
