from __future__ import annotations

import contextlib
import logging
import threading
from dataclasses import dataclass

from agent.config import settings
from agent.cli_backends.helpers import _get_agent_config


_SEMAPHORE_LOCK = threading.Lock()
_BACKEND_SEMAPHORES: dict[str, threading.BoundedSemaphore] = {}
_DEFAULT_BACKEND_PARALLEL_LIMITS: dict[str, int] = {
    "sgpt": 4,
    "ananta-worker": 4,
    "codex": 4,
    "opencode": 4,
    "aider": 1,
    "mistral_code": 1,
}


@dataclass(frozen=True)
class _SemaphoreTicket:
    backend: str
    acquired: bool
    limit: int


def _resolve_backend_parallel_limit(backend: str) -> int:
    agent_cfg = _get_agent_config()
    routing_cfg = dict(agent_cfg.get("sgpt_routing") or {})
    backend_limits = dict(routing_cfg.get("backend_parallel_limits") or {})
    configured = backend_limits.get(backend)
    if configured is None:
        configured = _DEFAULT_BACKEND_PARALLEL_LIMITS.get(backend, 1)
    try:
        return max(1, min(int(configured), 16))
    except Exception:
        return 1


def _get_backend_semaphore(backend: str, limit: int) -> threading.BoundedSemaphore:
    key = f"{backend}:{limit}"
    with _SEMAPHORE_LOCK:
        sem = _BACKEND_SEMAPHORES.get(key)
        if sem is None:
            sem = threading.BoundedSemaphore(limit)
            _BACKEND_SEMAPHORES[key] = sem
        return sem


@contextlib.contextmanager
def _acquire_backend_permit(backend: str, *, timeout: int):
    limit = _resolve_backend_parallel_limit(backend)
    sem = _get_backend_semaphore(backend, limit)
    acquired = sem.acquire(timeout=max(1, int(timeout)))
    if not acquired:
        logging.warning("Backend semaphore exhausted backend=%s limit=%s timeout=%ss", backend, limit, timeout)
        yield _SemaphoreTicket(backend=backend, acquired=False, limit=limit)
        return
    try:
        yield _SemaphoreTicket(backend=backend, acquired=True, limit=limit)
    finally:
        sem.release()
