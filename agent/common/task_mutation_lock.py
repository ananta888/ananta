"""Shared serialization primitive for Hub-owned task mutations.

This module is intentionally layer-neutral: repositories and services both
use the same lock instance without introducing a repository-to-service
dependency.

Acquisition is bounded. Every caller implements the fail-fast contract
(``acquired=False`` raises ``task_mutation_lock_unavailable``), so the port
must never block indefinitely: an unbounded acquire lets one stuck holder
freeze every other thread that touches the same task - including threads
that already hold unrelated locks such as the self-development journal
flock, which wedged whole admin operations.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import threading
import time
from collections.abc import Iterator
from typing import Any, Callable

from sqlalchemy import text

log = logging.getLogger(__name__)

_DEFAULT_ACQUIRE_TIMEOUT_SECONDS = 5.0
_ADVISORY_RETRY_SLEEP_SECONDS = 0.02


class TaskMutationLockPort:
    """Serialize mutations for one authoritative task identifier."""

    def __init__(
        self,
        *,
        engine_provider: Callable[[], Any] | None = None,
        acquire_timeout_seconds: float = _DEFAULT_ACQUIRE_TIMEOUT_SECONDS,
    ) -> None:
        self._engine_provider = engine_provider
        self._acquire_timeout_seconds = float(acquire_timeout_seconds)
        self._locks_guard = threading.Lock()
        self._local_locks: dict[str, threading.RLock] = {}
        self._thread_state = threading.local()

    def _engine(self):
        if self._engine_provider is not None:
            return self._engine_provider()
        from agent.database import engine

        return engine

    @staticmethod
    def _lock_id(task_id: str) -> int:
        digest = hashlib.sha256(f"task-status-mutation:{task_id}".encode("utf-8")).hexdigest()
        return int(digest[:15], 16)

    def local_lock(self, task_id: str) -> threading.RLock:
        normalized_task_id = str(task_id or "")
        with self._locks_guard:
            return self._local_locks.setdefault(
                normalized_task_id,
                threading.RLock(),
            )

    @contextlib.contextmanager
    def _held_local_lock(self, task_id: str) -> Iterator[bool]:
        """Bounded local acquire; reentrant for the holding thread itself."""
        lock = self.local_lock(task_id)
        if self._acquire_timeout_seconds > 0:
            acquired = lock.acquire(timeout=self._acquire_timeout_seconds)
        else:
            acquired = lock.acquire(blocking=False)
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            lock.release()

    def _acquire_advisory(self, connection: Any, lock_id: int) -> bool:
        """pg_try_advisory_lock in a bounded loop; never blocks the thread."""
        deadline = time.monotonic() + max(self._acquire_timeout_seconds, 0.0)
        while True:
            row = connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": lock_id},
            )
            if bool(row.scalar()):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(_ADVISORY_RETRY_SLEEP_SECONDS)

    @contextlib.contextmanager
    def distributed_lock(
        self,
        task_id: str,
    ) -> Iterator[bool]:
        """Acquire one shared PostgreSQL lock."""

        with self.distributed_locks([task_id]) as acquired:
            yield acquired

    @contextlib.contextmanager
    def distributed_locks(
        self,
        task_ids: list[str] | tuple[str, ...] | set[str],
    ) -> Iterator[bool]:
        """Acquire sorted advisory locks through one thread-shared connection.

        Acquisition uses ``pg_try_advisory_lock`` in a bounded loop: a lock
        held by a stuck thread surfaces as ``False`` after the acquire
        timeout instead of parking this thread forever.
        """

        engine = self._engine()
        if str(engine.dialect.name or "").strip().lower() != "postgresql":
            yield True
            return

        normalized_task_ids = sorted({str(task_id or "").strip() for task_id in task_ids if str(task_id or "").strip()})
        if not normalized_task_ids:
            yield True
            return
        held_locks = getattr(
            self._thread_state,
            "distributed_locks",
            None,
        )
        if held_locks is None:
            held_locks = {}
            self._thread_state.distributed_locks = held_locks
        connection = getattr(
            self._thread_state,
            "distributed_connection",
            None,
        )
        opened_connection = False
        acquired_ids: list[str] = []
        incremented_ids: list[str] = []
        try:
            if connection is None:
                connection = engine.connect()
                self._thread_state.distributed_connection = connection
                opened_connection = True
            for normalized_task_id in normalized_task_ids:
                existing = held_locks.get(normalized_task_id)
                if existing is not None:
                    existing["depth"] += 1
                    incremented_ids.append(normalized_task_id)
                    continue
                if not self._acquire_advisory(connection, self._lock_id(normalized_task_id)):
                    self._release_partial(connection, held_locks, incremented_ids, acquired_ids)
                    if opened_connection and connection is not None:
                        connection.close()
                        self._thread_state.distributed_connection = None
                    yield False
                    return
                held_locks[normalized_task_id] = {"depth": 1}
                acquired_ids.append(normalized_task_id)
        except Exception:
            log.exception(
                "task mutation advisory lock failed for %s",
                ",".join(normalized_task_ids),
            )
            self._release_partial(connection, held_locks, incremented_ids, acquired_ids)
            if opened_connection and connection is not None:
                connection.close()
                self._thread_state.distributed_connection = None
            yield False
            return

        try:
            yield True
        finally:
            for normalized_task_id in reversed(normalized_task_ids):
                state = held_locks.get(normalized_task_id)
                if state is None:
                    continue
                state["depth"] -= 1
                if state["depth"] > 0:
                    continue
                held_locks.pop(normalized_task_id, None)
                with contextlib.suppress(Exception):
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": self._lock_id(normalized_task_id)},
                    )
            if not held_locks and connection is not None:
                connection.close()
                self._thread_state.distributed_connection = None

    @staticmethod
    def _release_partial(
        connection: Any,
        held_locks: dict[str, dict[str, int]],
        incremented_ids: list[str],
        acquired_ids: list[str],
    ) -> None:
        for normalized_task_id in reversed(incremented_ids):
            state = held_locks.get(normalized_task_id)
            if state is not None:
                state["depth"] -= 1
                if state["depth"] <= 0:
                    held_locks.pop(normalized_task_id, None)
        for normalized_task_id in reversed(acquired_ids):
            held_locks.pop(normalized_task_id, None)
            with contextlib.suppress(Exception):
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": TaskMutationLockPort._lock_id(normalized_task_id)},
                )

    @contextlib.contextmanager
    def mutation_lock(
        self,
        task_id: str,
    ) -> Iterator[bool]:
        """Acquire local then distributed locks in one canonical order."""

        with self._held_local_lock(task_id) as local_acquired:
            if not local_acquired:
                yield False
                return
            with self.distributed_lock(task_id) as acquired:
                yield bool(acquired)

    @contextlib.contextmanager
    def mutation_locks(
        self,
        task_ids: list[str] | tuple[str, ...] | set[str],
    ) -> Iterator[bool]:
        """Acquire multiple task fences in canonical order with one checkout."""

        normalized_task_ids = sorted({str(task_id or "").strip() for task_id in task_ids if str(task_id or "").strip()})
        with contextlib.ExitStack() as stack:
            for task_id in normalized_task_ids:
                if not stack.enter_context(self._held_local_lock(task_id)):
                    yield False
                    return
            with self.distributed_locks(normalized_task_ids) as acquired:
                yield bool(acquired)


_port = TaskMutationLockPort()


def get_task_mutation_lock_port() -> TaskMutationLockPort:
    return _port


__all__ = [
    "TaskMutationLockPort",
    "get_task_mutation_lock_port",
]
