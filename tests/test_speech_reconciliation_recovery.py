from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import agent.common.context
import agent.repositories.speech_reconciliation as repository_module
import agent.services.background.speech_reconciliation_reconciler as reconciliation_background
from agent.services.background.speech_reconciliation_reconciler import (
    SpeechReconciliationRecoveryCandidate,
    SpeechReconciliationRecoveryReconciler,
    start_speech_reconciliation_reconciler_thread,
)


def _candidate(condition: str, number: int, **changes) -> SpeechReconciliationRecoveryCandidate:
    values = {
        "job_id": f"job-{number}",
        "attempt_id": f"attempt-{number}",
        "state": "running",
        "stage": "slow_asr",
        "expected_version": 2,
        "fencing_epoch": 3,
        "retry_count": 0,
        "max_retries": 2,
        "checkpoint_ref": f"artifact://speech-reconciliation-checkpoints/{number}",
        "condition": condition,
    }
    values.update(changes)
    return SpeechReconciliationRecoveryCandidate(**values)


class _Repository:
    def __init__(self, rows, *, conflict: str | None = None) -> None:
        self.rows = rows
        self.conflict = conflict
        self.actions = []

    def list_recovery_candidates(self, *, now_ms, limit):
        assert now_ms == 1000 and limit <= 1000
        return self.rows[:limit]

    def apply_recovery(self, candidate, action, *, authority):
        assert authority == "hub"
        self.actions.append((candidate, action))
        return candidate.job_id != self.conflict


def test_stale_attempt_retries_only_from_bound_checkpoint_and_old_fence_cannot_reactivate() -> None:
    rows = [
        _candidate("stale_heartbeat", 1),
        _candidate("stale_heartbeat", 2, checkpoint_ref=None),
        _candidate("stale_heartbeat", 3, retry_count=2),
    ]
    repository = _Repository(rows, conflict="job-3")
    summary = SpeechReconciliationRecoveryReconciler(repository, clock_ms=lambda: 1000).run_once()
    assert summary == type(summary)(scanned=3, applied=2, conflicts=1, retried=1, paused=0, cancelled=0, failed=1)
    retry = repository.actions[0][1]
    assert retry.target_state == "queued"
    assert retry.resume_checkpoint_ref == rows[0].checkpoint_ref
    assert retry.reason_code == "speech_reconciliation_stale_attempt_fenced"


def test_cancel_revoke_expiry_shutdown_live_pressure_and_unknown_are_idempotently_terminal() -> None:
    conditions = (
        "cancel_grace_elapsed",
        "consent_revoked",
        "job_expired",
        "shutdown",
        "live_pressure",
        "unknown",
    )
    rows = [_candidate(condition, index + 1) for index, condition in enumerate(conditions)]
    repository = _Repository(rows)
    summary = SpeechReconciliationRecoveryReconciler(repository, clock_ms=lambda: 1000).run_once()
    assert summary.applied == 6
    assert summary.cancelled == 3 and summary.paused == 2 and summary.failed == 1
    states = [action.target_state for _, action in repository.actions]
    assert states == ["cancelled", "cancelled", "expired", "paused", "paused", "failed"]


def test_lifecycle_composition_is_hub_feature_gated_and_starts_exactly_one_thread(monkeypatch) -> None:
    class _Thread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon
            self.started = False

        def start(self):
            self.started = True

        def is_alive(self):
            return self.started

    repository = _Repository([])
    app = SimpleNamespace(extensions={}, app_context=nullcontext)
    monkeypatch.setattr(reconciliation_background.settings, "role", "hub")
    monkeypatch.setattr(reconciliation_background, "_feature_enabled", lambda: True)
    monkeypatch.setattr(repository_module, "SpeechReconciliationRepository", lambda: repository)
    monkeypatch.setattr(reconciliation_background.threading, "Thread", _Thread)
    monkeypatch.setattr(agent.common.context, "active_threads", [])

    start_speech_reconciliation_reconciler_thread(app)
    state = app.extensions["speech_reconciliation_reconciler"]
    assert state["repository"] is repository
    assert state["thread"].started is True
    assert agent.common.context.active_threads == [state["thread"]]

    start_speech_reconciliation_reconciler_thread(app)
    assert agent.common.context.active_threads == [state["thread"]]
