"""Per-snake event broadcaster for Server-Sent Events (SSE).

Provides a small in-memory pub/sub so that backend components (e.g. the
VisualGuideService) can push typed events to connected browser clients
without blocking request handlers.  Each snake has a bounded queue;
listeners read via the SSE endpoint in snakes_execution_routes.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any

_log = logging.getLogger(__name__)

_MAX_QUEUE_SIZE = 128
_LOCK = threading.Lock()
_QUEUES: dict[str, queue.Queue[dict[str, Any]]] = {}


def _get_queue(snake_id: str) -> queue.Queue[dict[str, Any]]:
    """Return (or create) the bounded event queue for snake_id."""
    with _LOCK:
        if snake_id not in _QUEUES:
            _QUEUES[snake_id] = queue.Queue(maxsize=_MAX_QUEUE_SIZE)
        return _QUEUES[snake_id]


def broadcast_snake_event(snake_id: str, event_type: str, payload: dict[str, Any]) -> None:
    """Enqueue a typed event for all listeners of snake_id.

    Events are JSON-serialisable dicts with:
      - type:   event type (e.g. "guide", "candidates", "region_explain_ready")
      - ts:     epoch seconds
      - payload: event-specific data (must be JSON-serialisable)

    If the queue is full the oldest event is dropped (backpressure).
    """
    if not snake_id:
        return
    q = _get_queue(snake_id)
    event = {"type": event_type, "ts": time.time(), "payload": payload}
    try:
        q.put_nowait(event)
    except queue.Full:
        try:
            q.get_nowait()
            q.put_nowait(event)
        except queue.Empty:
            pass
        except queue.Full:
            _log.debug("snake event queue still full for %s", snake_id)


def get_snake_event(snake_id: str, timeout: float = 15.0) -> dict[str, Any] | None:
    """Blocking read of the next event for snake_id.

    Returns None on timeout so the SSE loop can send keep-alive comments.
    """
    q = _get_queue(snake_id)
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None


def drop_snake_queue(snake_id: str) -> None:
    """Remove the queue for snake_id (e.g. on snake disconnect/delete)."""
    with _LOCK:
        _QUEUES.pop(snake_id, None)
