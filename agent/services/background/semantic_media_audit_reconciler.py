"""Bounded retention loop for content-free semantic-media audit events."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Mapping

_EXTENSION_KEY = "semantic_media_audit_reconciler"


def start_semantic_media_audit_reconciler_thread(app: Any) -> None:
    from agent.config import settings
    from agent.services.semantic_media_feature_flags import (
        resolve_semantic_media_feature_flags,
    )

    flags = resolve_semantic_media_feature_flags(os.environ)
    if settings.role != "hub" or not any(flags.values()):
        return
    extensions = getattr(app, "extensions", None)
    if not isinstance(extensions, dict):
        return
    existing = extensions.get(_EXTENSION_KEY)
    if isinstance(existing, Mapping):
        thread = existing.get("thread")
        if isinstance(thread, threading.Thread) and thread.is_alive():
            return
    service = extensions.get("semantic_media_audit_service")
    if service is None or not callable(getattr(service, "delete_expired", None)):
        raise RuntimeError("semantic_media_audit_service_unavailable")
    outbox = extensions.get("semantic_media_audit_outbox")
    if outbox is not None and not callable(getattr(outbox, "dispatch_pending", None)):
        raise RuntimeError("semantic_media_audit_outbox_unavailable")
    leases = extensions.get("semantic_lease_repository")
    if leases is not None and not callable(getattr(leases, "expire_due", None)):
        raise RuntimeError("semantic_lease_repository_unavailable")

    stop_event = threading.Event()
    interval = _interval_seconds()
    batch_size = _batch_size()

    def run() -> None:
        while not stop_event.is_set():
            try:
                with app.app_context():
                    expired_leases = (
                        int(leases.expire_due(limit=min(batch_size, 1_000)))
                        if leases is not None
                        else 0
                    )
                    dispatched = (
                        outbox.dispatch_pending(limit=batch_size)
                        if outbox is not None
                        else None
                    )
                    deleted = int(service.delete_expired(limit=batch_size))
                if dispatched is not None and (
                    dispatched.delivered or dispatched.replayed or dispatched.failed
                ):
                    logging.info(
                        "Semantic media audit outbox attempted=%s delivered=%s replayed=%s failed=%s pending=%s",
                        dispatched.attempted,
                        dispatched.delivered,
                        dispatched.replayed,
                        dispatched.failed,
                        dispatched.pending,
                    )
                if deleted:
                    logging.info(
                        "Semantic media audit retention deleted=%s",
                        deleted,
                    )
                if expired_leases:
                    logging.info(
                        "Semantic compute leases expired=%s",
                        expired_leases,
                    )
            except Exception as exc:  # bounded retry; no event payload is logged
                logging.warning(
                    "Semantic media audit retention unavailable: %s",
                    type(exc).__name__,
                )
            stop_event.wait(interval)

    thread = threading.Thread(
        target=run,
        name="semantic-media-audit-reconciler",
        daemon=True,
    )
    extensions[_EXTENSION_KEY] = {
        "thread": thread,
        "stop_event": stop_event,
    }
    import agent.common.context

    agent.common.context.active_threads.append(thread)
    thread.start()


def stop_semantic_media_audit_reconciler(app: Any) -> None:
    extensions = getattr(app, "extensions", None)
    state = extensions.get(_EXTENSION_KEY) if isinstance(extensions, dict) else None
    if not isinstance(state, Mapping):
        return
    stop_event = state.get("stop_event")
    if isinstance(stop_event, threading.Event):
        stop_event.set()


def _interval_seconds() -> float:
    raw = os.environ.get("ANANTA_SEMANTIC_MEDIA_AUDIT_RETENTION_INTERVAL_SECONDS", "300")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 300.0
    return max(5.0, min(value, 3600.0))


def _batch_size() -> int:
    raw = os.environ.get("ANANTA_SEMANTIC_MEDIA_AUDIT_RETENTION_BATCH_SIZE", "1000")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 1000
    return max(1, min(value, 10_000))


__all__ = [
    "start_semantic_media_audit_reconciler_thread",
    "stop_semantic_media_audit_reconciler",
]
