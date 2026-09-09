"""Bounded connection reuse must never reuse authorization read results."""

import sqlite3
import time

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import TimeoutError
from sqlalchemy.pool import NullPool, QueuePool

from agent.sqlite_connection_pool import file_sqlite_pool_options


@pytest.mark.parametrize("capacity", [-1, 65, True, None, "8", 1.5])
def test_invalid_pool_capacity_is_not_silently_repaired(capacity):
    with pytest.raises(ValueError, match="sqlite_pool_capacity_invalid"):
        file_sqlite_pool_options(capacity)


def test_default_preserves_unpooled_file_connections():
    assert file_sqlite_pool_options(0) == {"poolclass": NullPool}


def test_opt_in_is_finite_and_has_a_short_checkout_deadline():
    assert file_sqlite_pool_options(8) == {
        "poolclass": QueuePool,
        "pool_size": 8,
        "max_overflow": 0,
        "pool_timeout": 0.25,
    }


@pytest.mark.parametrize("capacity,expected_connections", [(0, 5), (1, 1)])
def test_five_fresh_sessions_reuse_only_the_configured_physical_connections(tmp_path, capacity, expected_connections):
    from sqlalchemy.orm import Session

    engine = create_engine(f"sqlite:///{tmp_path / 'owned-read-count.db'}", **file_sqlite_pool_options(capacity))
    opened = []

    @event.listens_for(engine, "connect")
    def connected(connection, _record):
        opened.append(connection)

    try:
        for _ in range(5):
            with Session(engine) as session:
                assert session.connection().exec_driver_sql("SELECT 1").scalar_one() == 1
        assert len(opened) == expected_connections
    finally:
        engine.dispose()


def test_reused_connection_reads_external_revocation_and_rolls_back_uncommitted_state(tmp_path):
    from agent.database import configure_sqlite_connection

    path = tmp_path / "owned-authority.db"
    engine = create_engine(f"sqlite:///{path}", **file_sqlite_pool_options(1))
    opened = []

    @event.listens_for(engine, "connect")
    def connected(connection, _record):
        configure_sqlite_connection(connection)
        opened.append(connection)

    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE authority (allowed INTEGER)")
            connection.exec_driver_sql("INSERT INTO authority VALUES (1)")
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT allowed FROM authority").scalar_one() == 1
            connection.exec_driver_sql("UPDATE authority SET allowed=9")
        with sqlite3.connect(path) as external:
            assert external.execute("SELECT allowed FROM authority").fetchone() == (1,)
            external.execute("UPDATE authority SET allowed=0")
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT allowed FROM authority").scalar_one() == 0
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert len(opened) == 1
    finally:
        engine.dispose()


def test_saturation_does_not_open_unbounded_connections_or_wait_for_a_person(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'owned-capacity.db'}", **file_sqlite_pool_options(1))
    try:
        with engine.connect():
            began = time.monotonic()
            with pytest.raises(TimeoutError):
                engine.connect()
            assert time.monotonic() - began < 2
            assert engine.pool.checkedout() == 1 and engine.pool.overflow() == 0
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1
    finally:
        engine.dispose()
