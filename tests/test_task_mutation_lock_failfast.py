"""Task mutation locks: bounded acquisition, never an indefinite freeze.

The port's callers implement a fail-fast contract (``acquired=False``
raises ``task_mutation_lock_unavailable``). The locks must therefore never
block indefinitely: an unbounded acquire let one stuck holder freeze every
other thread touching the same task - including threads already holding
the self-development journal flock, which wedged whole admin operations.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from agent.common.task_mutation_lock import TaskMutationLockPort


def _sqlite_engine():
    return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))


def _port(timeout: float = 0.05) -> TaskMutationLockPort:
    return TaskMutationLockPort(engine_provider=_sqlite_engine, acquire_timeout_seconds=timeout)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeConnection:
    def __init__(self, grants):
        self._grants = list(grants)
        self.executed = []
        self.closed = False

    def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append(sql)
        if "pg_try_advisory_lock" in sql:
            return _FakeResult(self._grants.pop(0) if self._grants else True)
        return _FakeResult(None)

    def close(self):
        self.closed = True


class _FakePostgresEngine:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self, connection):
        self._connection = connection

    def connect(self):
        return self._connection


def test_contended_acquisition_fails_fast_instead_of_blocking():
    port = _port(timeout=0.05)
    holding = threading.Event()
    release = threading.Event()

    def holder():
        with port.mutation_locks({"T"}) as acquired:
            assert acquired is True
            holding.set()
            release.wait(timeout=5)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert holding.wait(timeout=2)
        started = time.monotonic()
        with port.mutation_locks({"T"}) as acquired:
            assert acquired is False
        assert time.monotonic() - started < 2.0
    finally:
        release.set()
        thread.join(timeout=2)


def test_same_thread_nesting_stays_reentrant():
    port = _port(timeout=0.05)
    with port.mutation_locks({"T", "S"}) as outer:
        assert outer is True
        with port.mutation_locks({"T"}) as inner:
            assert inner is True
        with port.mutation_lock({"S"}) as single:
            assert single is True


def test_failed_acquisition_releases_earlier_locks_without_leak():
    port = _port(timeout=0.05)
    holding = threading.Event()
    release = threading.Event()

    def holder():
        with port.mutation_locks({"B"}) as acquired:
            assert acquired is True
            holding.set()
            release.wait(timeout=5)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert holding.wait(timeout=2)
        # Sorted order acquires "A" first, then fails on "B": "A" must not leak.
        with port.mutation_locks({"A", "B"}) as acquired:
            assert acquired is False
    finally:
        release.set()
        thread.join(timeout=2)

    with port.mutation_locks({"A"}) as acquired:
        assert acquired is True


def test_advisory_acquire_uses_try_lock_and_respects_grants():
    connection = _FakeConnection(grants=[True])
    port = TaskMutationLockPort(engine_provider=lambda: _FakePostgresEngine(connection), acquire_timeout_seconds=0.05)
    with port.mutation_locks({"T"}) as acquired:
        assert acquired is True
    assert any("pg_try_advisory_lock" in sql for sql in connection.executed)
    assert all("pg_advisory_lock(" not in sql for sql in connection.executed)
    assert any("pg_advisory_unlock" in sql for sql in connection.executed)
    assert connection.closed is True


def test_advisory_timeout_fails_fast_and_unwinds():
    connection = _FakeConnection(grants=[True, False])
    port = TaskMutationLockPort(engine_provider=lambda: _FakePostgresEngine(connection), acquire_timeout_seconds=0.0)
    # Nested same-thread depth: outer holds T, inner must not double-grant.
    with port.mutation_locks({"T"}) as outer:
        assert outer is True
        with port.mutation_locks({"T"}) as inner:
            assert inner is True
    # Next single acquire gets the second grant value (False) -> bounded False.
    with port.mutation_locks({"S"}) as acquired:
        assert acquired is False
    # And the failure path released nothing that blocks a fresh grant.
    connection._grants.append(True)
    with port.mutation_locks({"S"}) as acquired:
        assert acquired is True
