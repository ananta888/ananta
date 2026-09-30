"""Dependency-free speech adaptation admission values shared with persistence.

The Hub admission service (``agent.services.speech_adaptation_job_service``)
and the SQL decision store both use these value objects. They live in the
model layer so the repository does not import the service layer; the service
module re-exports them unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ananta_contracts.speech_adaptation import SpeechAdaptationJob, SpeechAdaptationResult


@dataclass(frozen=True)
class SpeechPrincipal:
    tenant_id: str
    subject: str

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.subject.strip():
            raise ValueError("speech principal requires tenant and subject")


@dataclass(frozen=True)
class SpeechCapacityLease:
    lease_id: str
    epoch: int
    expires_at_ms: int


class SpeechAdaptationDecisionConflict(RuntimeError):
    """Stable persistence conflict raised by a decision-store adapter."""


@dataclass(frozen=True)
class SpeechAdmissionDecision:
    job_id: str
    task_id: str
    status: str
    reason_code: str
    job: SpeechAdaptationJob | None
    request_digest: str
    admission_request: Mapping[str, Any] | None = None
    result: SpeechAdaptationResult | None = None


def restore_speech_adaptation_job(payload: Mapping[str, Any]) -> SpeechAdaptationJob:
    """Validate a persisted contract without pretending its deadline is new."""

    deadline = int(payload.get("deadline_at_ms") or 0)
    budget = payload.get("budget") if isinstance(payload.get("budget"), Mapping) else {}
    wall_ms = int(budget.get("max_wall_seconds") or 0) * 1000
    fencing = payload.get("fencing") if isinstance(payload.get("fencing"), Mapping) else {}
    lease_expiry = int(fencing.get("lease_expires_at_ms") or 0)
    historical_now = max(0, min(deadline - wall_ms, lease_expiry - wall_ms))
    return SpeechAdaptationJob.from_mapping(payload, now_ms=historical_now)


__all__ = [
    "SpeechAdaptationDecisionConflict",
    "SpeechAdmissionDecision",
    "SpeechCapacityLease",
    "SpeechPrincipal",
    "restore_speech_adaptation_job",
]
