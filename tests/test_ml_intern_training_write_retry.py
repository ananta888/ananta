from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy.exc import OperationalError

from agent.repositories import ml_intern_training_serialization as serialization


def _locked(message: str = "database table is locked") -> OperationalError:
    return OperationalError("UPDATE ml_intern_training_jobs", {}, sqlite3.OperationalError(message))


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(serialization, "_SQLITE_LOCK_BACKOFF_SECONDS", 0.0)


def test_a_transient_sqlite_lock_is_retried_until_the_write_succeeds() -> None:
    calls: list[int] = []

    @serialization.serialized_write
    def write() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise _locked()
        return "saved"

    assert write() == "saved"
    assert len(calls) == 3


def test_other_operational_errors_are_not_retried() -> None:
    calls: list[int] = []

    @serialization.serialized_write
    def write() -> None:
        calls.append(1)
        raise _locked("no such table: ml_intern_training_jobs")

    with pytest.raises(OperationalError, match="no such table"):
        write()
    assert len(calls) == 1


def test_a_lock_that_persists_fails_after_the_bounded_retries() -> None:
    calls: list[int] = []

    @serialization.serialized_write
    def write() -> None:
        calls.append(1)
        raise _locked("database is locked")

    with pytest.raises(OperationalError, match="database is locked"):
        write()
    assert len(calls) == serialization._SQLITE_LOCK_RETRIES + 1
