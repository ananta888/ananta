"""Bounded replay-outcome cache for task-scoped recovery dispatches."""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any

RECOVERY_OUTCOME_CACHE_LOCK = threading.Lock()
RECOVERY_OUTCOME_CACHE: dict[str, tuple[float, Any]] = {}


def recovery_cache_key(
    token: str | None,
    *,
    task_id: str,
    phase: str,
    request_fingerprint: str,
) -> str:
    return hashlib.sha256(
        (
            f"{str(task_id or '').strip()}\0"
            f"{str(phase or '').strip().lower()}\0"
            f"{str(request_fingerprint or '').strip()}\0"
            f"{str(token or '')}"
        ).encode("utf-8")
    ).hexdigest()


def cache_recovery_outcome(
    token: str | None,
    outcome: Any,
    *,
    task_id: str,
    phase: str,
    request_fingerprint: str,
) -> None:
    if not token:
        return
    now = time.time()
    with RECOVERY_OUTCOME_CACHE_LOCK:
        expired = [
            key
            for key, (stored_at, _value) in (
                RECOVERY_OUTCOME_CACHE.items()
            )
            if now - stored_at > 1800.0
        ]
        for key in expired:
            RECOVERY_OUTCOME_CACHE.pop(key, None)
        while len(RECOVERY_OUTCOME_CACHE) >= 256:
            oldest = min(
                RECOVERY_OUTCOME_CACHE,
                key=lambda key: RECOVERY_OUTCOME_CACHE[key][0],
            )
            RECOVERY_OUTCOME_CACHE.pop(oldest, None)
        RECOVERY_OUTCOME_CACHE[
            recovery_cache_key(
                token,
                task_id=task_id,
                phase=phase,
                request_fingerprint=request_fingerprint,
            )
        ] = (now, outcome)


def cached_recovery_outcome(
    token: str | None,
    *,
    task_id: str,
    phase: str,
    request_fingerprint: str,
) -> Any | None:
    if not token:
        return None
    with RECOVERY_OUTCOME_CACHE_LOCK:
        cached = RECOVERY_OUTCOME_CACHE.get(
            recovery_cache_key(
                token,
                task_id=task_id,
                phase=phase,
                request_fingerprint=request_fingerprint,
            )
        )
    if cached is None or time.time() - cached[0] > 1800.0:
        return None
    return cached[1]
