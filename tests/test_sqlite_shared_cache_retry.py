from __future__ import annotations

import threading
import time
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool


def _shared_cache_engine():
    url = f"sqlite:///file:lock-retry-{uuid.uuid4().hex}?mode=memory&cache=shared&uri=true"
    # NullPool: every connect() is a separate SQLite connection (the default pool would share one per thread)
    return create_engine(url, connect_args={"check_same_thread": False}, poolclass=NullPool)


def test_a_statement_blocked_by_a_shared_cache_table_lock_is_retried_until_the_lock_is_released() -> None:
    engine = _shared_cache_engine()
    keeper = engine.connect()  # keeps the in-memory database alive
    keeper.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)"))
    keeper.execute(text("INSERT INTO items (name) VALUES ('committed')"))
    keeper.commit()

    writer = engine.connect()
    writer.execute(text("INSERT INTO items (name) VALUES ('pending')"))  # open write transaction: table locked

    def release() -> None:
        time.sleep(0.2)
        writer.commit()

    releaser = threading.Thread(target=release)
    releaser.start()
    started = time.monotonic()
    with engine.connect() as reader:  # without the retry: "database table is locked" at once
        count = reader.execute(text("SELECT count(*) FROM items")).scalar_one()
    waited = time.monotonic() - started
    releaser.join()

    assert waited >= 0.15  # it waited for the lock instead of failing at once
    assert count == 2
    writer.close()
    keeper.close()
    engine.dispose()


def test_other_operational_errors_are_not_retried() -> None:
    engine = _shared_cache_engine()
    with engine.connect() as connection:
        started = time.monotonic()
        with pytest.raises(Exception, match="no such table"):
            connection.execute(text("SELECT * FROM missing_table"))
        assert time.monotonic() - started < 0.1
    engine.dispose()
