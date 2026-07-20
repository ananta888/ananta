"""Dependency-light lifecycle contract for Hub-owned speech reconciliation."""

from __future__ import annotations

from dataclasses import dataclass

JOB_STATES = frozenset(
    {
        "queued",
        "running",
        "paused",
        "cancel_requested",
        "completed",
        "dataset_only_completed",
        "failed",
        "cancelled",
        "expired",
    }
)
TERMINAL_STATES = frozenset({"completed", "dataset_only_completed", "failed", "cancelled", "expired"})
STAGES = frozenset(
    {
        "admission",
        "staging",
        "slow_asr",
        "alignment",
        "resolution",
        "dataset",
        "training_delegation",
        "evaluation",
        "finalization",
    }
)

TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"running", "paused", "cancel_requested", "cancelled", "expired", "failed"}),
    "running": frozenset(
        {
            "paused",
            "cancel_requested",
            "completed",
            "dataset_only_completed",
            "failed",
            "expired",
        }
    ),
    "paused": frozenset({"queued", "running", "cancel_requested", "cancelled", "expired", "failed"}),
    "cancel_requested": frozenset({"cancelled", "failed", "expired"}),
    "completed": frozenset(),
    "dataset_only_completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "expired": frozenset(),
}


@dataclass(frozen=True)
class SpeechReconciliationTransition:
    previous_state: str
    target_state: str
    stage: str
    reason_code: str
    duplicate: bool = False


def validate_state(value: str) -> str:
    if value not in JOB_STATES:
        raise ValueError("speech_reconciliation_state_invalid")
    return value


def validate_stage(value: str) -> str:
    if value not in STAGES:
        raise ValueError("speech_reconciliation_stage_invalid")
    return value


__all__ = [
    "JOB_STATES",
    "STAGES",
    "TERMINAL_STATES",
    "TRANSITIONS",
    "SpeechReconciliationTransition",
    "validate_stage",
    "validate_state",
]
