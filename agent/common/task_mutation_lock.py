"""Shared serialization primitive for Hub-owned task mutations.

This module is intentionally layer-neutral: repositories and services both
use the same lock instance without introducing a repository-to-service
dependency.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import threading
from collections.abc import Iterator
from typing import Any, Callable

from sqlalchemy import text

log = logging.getLogger(__name__)


class TaskMutationLockPort:
    """Serialize mutations for one authoritative task identifier."""

    def __init__(
        self,
        *,
        engine_provider: Callable[[], Any] | None = None,
    ) -> None:
        self._engine_provider = engine_provider
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
        digest = hashlib.sha256(
            f"task-status-mutation:{task_id}".encode("utf-8")
        ).hexdigest()
        return int(digest[:15], 16)

    def local_lock(self, task_id: str) -> threading.RLock:
        normalized_task_id = str(task_id or "")
        with self._locks_guard:
            return self._local_locks.setdefault(
                normalized_task_id,
                threading.RLock(),
            )

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
        """Acquire sorted advisory locks through one thread-shared connection."""

        engine = self._engine()
        if (
            str(engine.dialect.name or "").strip().lower()
            != "postgresql"
        ):
            yield True
            return

        normalized_task_ids = sorted(
            {
                str(task_id or "").strip()
                for task_id in task_ids
                if str(task_id or "").strip()
            }
        )
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
                self._thread_state.distributed_connection = (
                    connection
                )
                opened_connection = True
            for normalized_task_id in normalized_task_ids:
                existing = held_locks.get(normalized_task_id)
                if existing is not None:
                    existing["depth"] += 1
                    incremented_ids.append(normalized_task_id)
                    continue
                connection.execute(
                    text("SELECT pg_advisory_lock(:lock_id)"),
                    {
                        "lock_id": self._lock_id(
                            normalized_task_id
                        )
                    },
                )
                held_locks[normalized_task_id] = {"depth": 1}
                acquired_ids.append(normalized_task_id)
        except Exception:
            log.exception(
                "task mutation advisory lock failed for %s",
                ",".join(normalized_task_ids),
            )
            for normalized_task_id in reversed(
                incremented_ids
            ):
                held_locks[normalized_task_id]["depth"] -= 1
            for normalized_task_id in reversed(acquired_ids):
                held_locks.pop(normalized_task_id, None)
                with contextlib.suppress(Exception):
                    connection.execute(
                        text(
                            "SELECT pg_advisory_unlock(:lock_id)"
                        ),
                        {
                            "lock_id": self._lock_id(
                                normalized_task_id
                            )
                        },
                    )
            if opened_connection and connection is not None:
                connection.close()
                self._thread_state.distributed_connection = None
            yield False
            return

        try:
            yield True
        finally:
            for normalized_task_id in reversed(
                normalized_task_ids
            ):
                state = held_locks.get(normalized_task_id)
                if state is None:
                    continue
                state["depth"] -= 1
                if state["depth"] > 0:
                    continue
                held_locks.pop(normalized_task_id, None)
                with contextlib.suppress(Exception):
                    connection.execute(
                        text(
                            "SELECT pg_advisory_unlock(:lock_id)"
                        ),
                        {
                            "lock_id": self._lock_id(
                                normalized_task_id
                            )
                        },
                    )
            if not held_locks and connection is not None:
                connection.close()
                self._thread_state.distributed_connection = None

    @contextlib.contextmanager
    def mutation_lock(
        self,
        task_id: str,
    ) -> Iterator[bool]:
        """Acquire local then distributed locks in one canonical order."""

        with self.local_lock(task_id):
            with self.distributed_lock(task_id) as acquired:
                yield bool(acquired)

    @contextlib.contextmanager
    def mutation_locks(
        self,
        task_ids: list[str] | tuple[str, ...] | set[str],
    ) -> Iterator[bool]:
        """Acquire multiple task fences in canonical order with one checkout."""

        normalized_task_ids = sorted(
            {
                str(task_id or "").strip()
                for task_id in task_ids
                if str(task_id or "").strip()
            }
        )
        with contextlib.ExitStack() as stack:
            for task_id in normalized_task_ids:
                stack.enter_context(self.local_lock(task_id))
            with self.distributed_locks(
                normalized_task_ids
            ) as acquired:
                yield bool(acquired)


_port = TaskMutationLockPort()


def get_task_mutation_lock_port() -> TaskMutationLockPort:
    return _port


__all__ = [
    "TaskMutationLockPort",
    "get_task_mutation_lock_port",
]
