"""Recovery port between the Hub reconciler and its persistence adapter.

The Hub recovery reconciler (service layer) decides; the repository projects
persisted facts into :class:`SpeechReconciliationRecoveryCandidate` values and
applies :class:`SpeechReconciliationRecoveryAction` values with CAS. Both
value objects and the structural port are dependency-free, so the repository
implements the port without importing the service layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True, slots=True)
class SpeechReconciliationRecoveryCandidate:
    job_id: str
    attempt_id: str
    state: str
    stage: str
    expected_version: int
    fencing_epoch: int
    retry_count: int
    max_retries: int
    checkpoint_ref: str | None
    condition: str


@dataclass(frozen=True, slots=True)
class SpeechReconciliationRecoveryAction:
    action: str
    target_state: str
    reason_code: str
    resume_checkpoint_ref: str | None = None


class SpeechReconciliationRecoveryPort(Protocol):
    def list_recovery_candidates(
        self,
        *,
        now_ms: int,
        limit: int,
    ) -> Sequence[SpeechReconciliationRecoveryCandidate]: ...

    def apply_recovery(
        self,
        candidate: SpeechReconciliationRecoveryCandidate,
        action: SpeechReconciliationRecoveryAction,
        *,
        authority: str,
    ) -> bool: ...


__all__ = [
    "SpeechReconciliationRecoveryAction",
    "SpeechReconciliationRecoveryCandidate",
    "SpeechReconciliationRecoveryPort",
]
