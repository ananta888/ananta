"""Model-analysis job lifecycle states, limits and records (dependency-free)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ananta_contracts.model_intelligence import AnalysisJob
from ananta_contracts.model_intelligence_execution import (
    AnalysisCompletion,
    ResourceLease,
)


class ModelAnalysisJobState(str, Enum):
    SUBMISSION_PENDING = "submission_pending"
    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset(
    {
        ModelAnalysisJobState.SUCCEEDED,
        ModelAnalysisJobState.FAILED,
        ModelAnalysisJobState.CANCELLED,
    }
)
QUEUED_STATES = frozenset(
    {
        ModelAnalysisJobState.SUBMISSION_PENDING,
        ModelAnalysisJobState.QUEUED,
    }
)


class ModelAnalysisJobServiceError(RuntimeError):
    def __init__(self, reason_code: str, *, retryable: bool = False) -> None:
        self.reason_code = reason_code
        self.retryable = retryable
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class ModelAnalysisLimits:
    max_global_queued: int = 128
    max_tenant_queued: int = 16
    max_tenant_active: int = 32
    max_attempts: int = 3
    max_lease_seconds: int = 3600
    max_worker_memory_bytes: int = 64 * 1024**3

    def __post_init__(self) -> None:
        for value in (
            self.max_global_queued,
            self.max_tenant_queued,
            self.max_tenant_active,
            self.max_attempts,
            self.max_lease_seconds,
            self.max_worker_memory_bytes,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("model_analysis_limits_invalid")


@dataclass(frozen=True, slots=True)
class ModelAnalysisJobRecord:
    job: AnalysisJob
    state: ModelAnalysisJobState
    version: int
    attempt: int
    lease: ResourceLease | None
    completion: AnalysisCompletion | None
    reason_code: str
    projection_pending: bool
    updated_epoch_ms: int


@dataclass(frozen=True, slots=True)
class ModelAnalysisRecoverySummary:
    scanned: int = 0
    recovered: int = 0
    requeued: int = 0
    failed: int = 0
    cancelled: int = 0
    conflicts: int = 0


@dataclass(frozen=True, slots=True)
class ModelAnalysisJobPage:
    items: tuple[ModelAnalysisJobRecord, ...]
    next_cursor: str | None


__all__ = [
    "ModelAnalysisJobPage",
    "ModelAnalysisJobRecord",
    "ModelAnalysisJobServiceError",
    "ModelAnalysisJobState",
    "ModelAnalysisLimits",
    "ModelAnalysisRecoverySummary",
    "QUEUED_STATES",
    "TERMINAL_STATES",
]
