"""Bounded Hub recovery decisions for offline speech reconciliation."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from agent.config import settings
from agent.services.semantic_media_audit_service import SemanticMediaAuditPort

_EXTENSION_KEY = "speech_reconciliation_reconciler"


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


@dataclass(frozen=True, slots=True)
class SpeechReconciliationRecoverySummary:
    scanned: int
    applied: int
    conflicts: int
    retried: int
    paused: int
    cancelled: int
    failed: int


class SpeechReconciliationRecoveryReconciler:
    """Maps persisted facts to idempotent CAS actions; it owns no worker loop."""

    def __init__(
        self,
        repository: SpeechReconciliationRecoveryPort,
        *,
        clock_ms: Callable[[], int] | None = None,
        audit: SemanticMediaAuditPort | None = None,
    ) -> None:
        self._repository = repository
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._audit = audit

    def run_once(self, *, batch_size: int = 100) -> SpeechReconciliationRecoverySummary:
        if not 1 <= batch_size <= 1000:
            raise ValueError("speech_reconciliation_recovery_batch_invalid")
        candidates = tuple(self._repository.list_recovery_candidates(now_ms=self._clock_ms(), limit=batch_size))
        counts = {"applied": 0, "conflicts": 0, "retried": 0, "paused": 0, "cancelled": 0, "failed": 0}
        for candidate in candidates:
            action = self._decide(candidate)
            if self._repository.apply_recovery(candidate, action, authority="hub"):
                self._record_audit(candidate, action)
                counts["applied"] += 1
                counts[action.action] += 1
            else:
                counts["conflicts"] += 1
        return SpeechReconciliationRecoverySummary(scanned=len(candidates), **counts)

    def _record_audit(
        self,
        candidate: SpeechReconciliationRecoveryCandidate,
        action: SpeechReconciliationRecoveryAction,
    ) -> None:
        if self._audit is None or bool(getattr(self._repository, "transactional_audit", False)):
            return
        binding = getattr(self._repository, "attempt_audit_binding", lambda _attempt_id: None)(
            candidate.attempt_id
        )
        if binding is None:
            raise RuntimeError("semantic_audit_binding_unavailable")
        tenant_id, job_id, epoch, persisted_reason = binding
        event = self._audit.prepare_transition(
            idempotency_key=(
                f"speech-reconciliation-recovery:{job_id}:"
                f"{candidate.attempt_id}:{action.target_state}:{persisted_reason}"
            ),
            tenant_id=tenant_id,
            scope=f"speech-job:{job_id}",
            event_type="semantic_recovery",
            transition=action.target_state,
            reason_code=persisted_reason,
            epoch=epoch,
            lease_ref=candidate.attempt_id,
            job_ref=job_id,
        )
        self._audit.append_prepared(event)

    @staticmethod
    def _decide(candidate: SpeechReconciliationRecoveryCandidate) -> SpeechReconciliationRecoveryAction:
        if candidate.condition == "stale_heartbeat":
            checkpoint = candidate.checkpoint_ref
            if (
                candidate.retry_count < candidate.max_retries
                and checkpoint is not None
                and checkpoint.startswith("artifact://speech-reconciliation-checkpoints/")
                and ".." not in checkpoint.split("/")
            ):
                return SpeechReconciliationRecoveryAction(
                    "retried",
                    "queued",
                    "speech_reconciliation_stale_attempt_fenced",
                    checkpoint,
                )
            return SpeechReconciliationRecoveryAction("failed", "failed", "speech_reconciliation_retry_exhausted")
        if candidate.condition in {"cancel_grace_elapsed", "consent_revoked", "job_expired"}:
            return SpeechReconciliationRecoveryAction(
                "cancelled",
                "cancelled" if candidate.condition != "job_expired" else "expired",
                f"speech_reconciliation_{candidate.condition}",
            )
        if candidate.condition in {"shutdown", "live_pressure"}:
            return SpeechReconciliationRecoveryAction(
                "paused", "paused", f"speech_reconciliation_{candidate.condition}"
            )
        return SpeechReconciliationRecoveryAction(
            "failed", "failed", "speech_reconciliation_recovery_condition_unknown"
        )


def start_speech_reconciliation_reconciler_thread(app: Any) -> None:
    """Start the Hub recovery loop exactly once per app."""

    # Recovery must also run while the deployment kill switch is disabled so
    # persisted stale/revoked attempts from a previous process are fenced.
    if settings.role != "hub":
        return
    extensions = getattr(app, "extensions", None)
    if isinstance(extensions, dict):
        existing = extensions.get(_EXTENSION_KEY)
        if isinstance(existing, Mapping):
            thread = existing.get("thread")
            if isinstance(thread, threading.Thread) and thread.is_alive():
                return

    from agent.repositories.speech_reconciliation import SpeechReconciliationRepository

    audit = extensions.get("semantic_media_audit_recorder") if isinstance(extensions, dict) else None
    repository = SpeechReconciliationRepository()
    configure_audit = getattr(repository, "configure_audit", None)
    if callable(configure_audit):
        configure_audit(audit)
    service = SpeechReconciliationRecoveryReconciler(
        repository,
        audit=audit,
    )
    interval = _interval_seconds()
    stop_event = threading.Event()

    def run() -> None:
        import agent.common.context

        while not stop_event.is_set() and not agent.common.context.shutdown_requested:
            try:
                with app.app_context():
                    summary = service.run_once()
                if summary.applied or summary.conflicts:
                    logging.info(
                        "Speech reconciliation recovery: scanned=%s applied=%s "
                        "retried=%s paused=%s cancelled=%s failed=%s conflicts=%s",
                        summary.scanned,
                        summary.applied,
                        summary.retried,
                        summary.paused,
                        summary.cancelled,
                        summary.failed,
                        summary.conflicts,
                    )
            except Exception as exc:  # persistent state is retried next bounded tick
                logging.warning(
                    "Speech reconciliation recovery unavailable: %s",
                    type(exc).__name__,
                )
            if not _wait(interval, stop_event=stop_event):
                break

    thread = threading.Thread(
        target=run,
        name="speech-reconciliation-reconciler",
        daemon=True,
    )
    if isinstance(extensions, dict):
        extensions[_EXTENSION_KEY] = {
            "repository": repository,
            "service": service,
            "thread": thread,
            "stop_event": stop_event,
        }
    import agent.common.context

    agent.common.context.active_threads.append(thread)
    thread.start()


def stop_speech_reconciliation_reconciler(
    app: Any,
    *,
    join_timeout: float = 1.0,
) -> None:
    extensions = getattr(app, "extensions", None)
    state = extensions.get(_EXTENSION_KEY) if isinstance(extensions, Mapping) else None
    if not isinstance(state, Mapping):
        return
    stop_event = state.get("stop_event")
    thread = state.get("thread")
    if isinstance(stop_event, threading.Event):
        stop_event.set()
    if isinstance(thread, threading.Thread) and thread is not threading.current_thread() and thread.is_alive():
        thread.join(timeout=max(0.0, min(float(join_timeout), 10.0)))


def _feature_enabled() -> bool:
    from agent.services.semantic_media_feature_flags import resolve_semantic_media_feature_flags

    return resolve_semantic_media_feature_flags(os.environ).get("speech_reconciliation", False)


def _interval_seconds() -> float:
    raw = os.environ.get("ANANTA_SPEECH_RECONCILIATION_RECONCILE_INTERVAL_SECONDS", "5")
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        parsed = 5.0
    return max(0.25, min(parsed, 300.0))


def _wait(seconds: float, *, stop_event: threading.Event | None = None) -> bool:
    import agent.common.context

    deadline = time.monotonic() + seconds
    while not agent.common.context.shutdown_requested and not (stop_event is not None and stop_event.is_set()):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(0.25, remaining))
    return False


__all__ = [
    "SpeechReconciliationRecoveryAction",
    "SpeechReconciliationRecoveryCandidate",
    "SpeechReconciliationRecoveryPort",
    "SpeechReconciliationRecoveryReconciler",
    "SpeechReconciliationRecoverySummary",
    "start_speech_reconciliation_reconciler_thread",
    "stop_speech_reconciliation_reconciler",
]
