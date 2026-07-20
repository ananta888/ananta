"""Bounded background entry point for speech evidence retention."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Mapping

from agent.services.speech_evidence_retention_cleanup_service import (
    SpeechEvidenceCleanupSummary,
    SpeechEvidenceRetentionCleanupService,
)


class SpeechEvidenceRetentionReconciler:
    def __init__(self, service: SpeechEvidenceRetentionCleanupService | None = None) -> None:
        self._service = service or SpeechEvidenceRetentionCleanupService()

    def run_once(self, *, batch_size: int = 100) -> SpeechEvidenceCleanupSummary:
        return self._service.run_once(limit=batch_size)


_EXTENSION_KEY = "speech_evidence_retention_reconciler"


def start_speech_evidence_retention_reconciler_thread(app: Any) -> None:
    """Start one bounded Hub-owned cleanup loop when governance is enabled."""

    from agent.config import settings

    if settings.role != "hub" or not _feature_enabled():
        return
    extensions = getattr(app, "extensions", None)
    if not isinstance(extensions, dict):
        return
    existing = extensions.get(_EXTENSION_KEY)
    if isinstance(existing, Mapping):
        thread = existing.get("thread")
        if isinstance(thread, threading.Thread) and thread.is_alive():
            return

    stop_event = threading.Event()
    service = SpeechEvidenceRetentionReconciler(
        SpeechEvidenceRetentionCleanupService(
            audit=extensions.get("semantic_media_audit_recorder"),
        )
    )
    interval = _interval_seconds()
    batch_size = _batch_size()

    def run() -> None:
        while not stop_event.is_set():
            try:
                with app.app_context():
                    summary = service.run_once(batch_size=batch_size)
                if summary.staged or summary.completed or summary.failed:
                    logging.info(
                        "Speech evidence retention: staged=%s completed=%s "
                        "pending=%s skipped=%s failed=%s",
                        summary.staged,
                        summary.completed,
                        summary.pending,
                        summary.skipped_active_references,
                        summary.failed,
                    )
            except Exception as exc:  # durable state is retried on the next tick
                logging.warning(
                    "Speech evidence retention unavailable: %s",
                    type(exc).__name__,
                )
            stop_event.wait(interval)

    thread = threading.Thread(
        target=run,
        name="speech-evidence-retention-reconciler",
        daemon=True,
    )
    extensions[_EXTENSION_KEY] = {
        "service": service,
        "thread": thread,
        "stop_event": stop_event,
    }
    import agent.common.context

    agent.common.context.active_threads.append(thread)
    thread.start()


def stop_speech_evidence_retention_reconciler(app: Any) -> None:
    extensions = getattr(app, "extensions", None)
    state = extensions.get(_EXTENSION_KEY) if isinstance(extensions, dict) else None
    if not isinstance(state, Mapping):
        return
    stop_event = state.get("stop_event")
    if isinstance(stop_event, threading.Event):
        stop_event.set()


def _feature_enabled() -> bool:
    from agent.services.semantic_media_feature_flags import (
        resolve_semantic_media_feature_flags,
    )

    flags = resolve_semantic_media_feature_flags(os.environ)
    return bool(
        flags.get("peer_evidence_sync")
        or flags.get("speech_reconciliation")
        or flags.get("speech_adaptation_training")
    )


def _interval_seconds() -> float:
    raw = os.environ.get("ANANTA_SPEECH_EVIDENCE_RETENTION_INTERVAL_SECONDS", "60")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 60.0
    return max(1.0, min(value, 3600.0))


def _batch_size() -> int:
    raw = os.environ.get("ANANTA_SPEECH_EVIDENCE_RETENTION_BATCH_SIZE", "100")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 100
    return max(1, min(value, 1000))


__all__ = [
    "SpeechEvidenceRetentionReconciler",
    "start_speech_evidence_retention_reconciler_thread",
    "stop_speech_evidence_retention_reconciler",
]
