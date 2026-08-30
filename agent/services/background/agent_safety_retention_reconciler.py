"""Lifecycle-owned automatic cleanup loop for expired safety freezes."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Mapping

_EXTENSION_KEY = "agent_safety_retention_reconciler"


def start_agent_safety_retention_reconciler(app: Any) -> None:
    extensions = getattr(app, "extensions", None)
    if not isinstance(extensions, dict) or "agent_safety_retention_service" not in extensions:
        return
    existing = extensions.get(_EXTENSION_KEY)
    if isinstance(existing, Mapping):
        thread = existing.get("thread")
        if isinstance(thread, threading.Thread) and thread.is_alive():
            return
    stop_event = threading.Event()
    interval = _interval_seconds()

    def run() -> None:
        while not stop_event.is_set():
            try:
                with app.app_context():
                    result = extensions["agent_safety_retention_service"].sweep_expired()
                if result["candidate_count"]:
                    logging.info(
                        "Agent safety retention: candidates=%s receipts=%s",
                        result["candidate_count"],
                        result["receipt_count"],
                    )
            except Exception as exc:
                logging.warning("Agent safety retention unavailable: %s", type(exc).__name__)
            stop_event.wait(interval)

    thread = threading.Thread(target=run, name="agent-safety-retention", daemon=True)
    extensions[_EXTENSION_KEY] = {"thread": thread, "stop_event": stop_event}
    import agent.common.context

    agent.common.context.active_threads.append(thread)
    thread.start()


def stop_agent_safety_retention_reconciler(app: Any) -> None:
    extensions = getattr(app, "extensions", None)
    state = extensions.get(_EXTENSION_KEY) if isinstance(extensions, dict) else None
    if isinstance(state, Mapping) and isinstance(state.get("stop_event"), threading.Event):
        state["stop_event"].set()


def _interval_seconds() -> float:
    try:
        value = float(os.environ.get("ANANTA_AGENT_SAFETY_RETENTION_INTERVAL_SECONDS", "60"))
    except (TypeError, ValueError):
        value = 60.0
    return max(1.0, min(value, 3_600.0))


__all__ = ["start_agent_safety_retention_reconciler", "stop_agent_safety_retention_reconciler"]
