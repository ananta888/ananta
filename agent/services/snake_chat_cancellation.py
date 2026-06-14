"""Cancellation registry for hub-owned AI-Snake chat runs."""
from __future__ import annotations

import threading
from collections.abc import Iterable

_LOCK = threading.Lock()
_EVENTS: dict[str, threading.Event] = {}


def register_chat_cancel(keys: Iterable[str]) -> threading.Event:
    """Register one cancellation event under all non-empty keys."""
    event = threading.Event()
    clean_keys = [str(key).strip() for key in keys if str(key).strip()]
    with _LOCK:
        for key in clean_keys:
            _EVENTS[key] = event
    return event


def unregister_chat_cancel(keys: Iterable[str], event: threading.Event | None = None) -> None:
    """Remove registered keys, preserving newer events for reused keys."""
    clean_keys = [str(key).strip() for key in keys if str(key).strip()]
    with _LOCK:
        for key in clean_keys:
            if event is None or _EVENTS.get(key) is event:
                _EVENTS.pop(key, None)


def cancel_chat(keys: Iterable[str]) -> list[str]:
    """Signal cancellation for matching keys and return the keys that were active."""
    cancelled: list[str] = []
    clean_keys = [str(key).strip() for key in keys if str(key).strip()]
    with _LOCK:
        for key in clean_keys:
            event = _EVENTS.get(key)
            if event is None:
                continue
            event.set()
            cancelled.append(key)
    return cancelled


def is_chat_cancelled(event: threading.Event | None) -> bool:
    return bool(event and event.is_set())
