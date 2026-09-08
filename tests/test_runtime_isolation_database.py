"""Explicit file WAL is independently owned; default named-memory remains intact."""

import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from tests.isolation_database import isolated_database_url


def test_default_database_is_named_memory_owned_by_this_process():
    url = make_url(isolated_database_url("memory"))
    assert url.database == "file:ananta-pytest-" + str(os.getpid())
    assert dict(url.query) == {"mode": "memory", "cache": "shared", "uri": "true"}


@pytest.mark.parametrize("mode", ["", "file", "sqlite:///data/ananta.db", "../runtime", None])
def test_unknown_modes_never_become_database_urls(mode):
    with pytest.raises(RuntimeError, match="^pytest_database_mode_invalid$"):
        isolated_database_url(mode)


def test_wal_directory_is_private_stable_only_within_this_process_and_independent_of_runtime():
    url = isolated_database_url("wal")
    path = Path(make_url(url).database)
    assert path.is_absolute() and path.name == "test.db"
    assert path.parent.name.startswith(f"ananta-pytest-db-{os.getpid()}-")
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert path.parent.stat().st_uid == os.geteuid()
    assert isolated_database_url("wal") == url
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from tests.isolation_database import isolated_database_url; print(isolated_database_url('wal'))",
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    assert result.stdout.strip() != url


def test_actual_harness_engine_uses_the_selected_sqlite_journal_mode(app):
    from agent.database import engine

    with engine.connect() as connection:
        expected = "wal" if os.environ.get("ANANTA_TEST_DATABASE_MODE", "memory") == "wal" else "memory"
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == expected


def test_production_wal_connection_allows_writer_while_an_independent_reader_holds_snapshot(tmp_path):
    from agent.database import configure_sqlite_connection

    path = tmp_path / "owned-concurrency.db"
    reader = sqlite3.connect(path, timeout=0.1)
    writer = sqlite3.connect(path, timeout=0.1)
    try:
        for connection in (reader, writer):
            configure_sqlite_connection(connection)
            assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        reader.execute("CREATE TABLE observed (value INTEGER)")
        reader.execute("INSERT INTO observed VALUES (1)")
        reader.commit()
        reader.execute("BEGIN")
        assert reader.execute("SELECT value FROM observed").fetchone() == (1,)
        writer.execute("UPDATE observed SET value=2")
        writer.commit()
        assert reader.execute("SELECT value FROM observed").fetchone() == (1,)
        reader.rollback()
        assert reader.execute("SELECT value FROM observed").fetchone() == (2,)
    finally:
        reader.close()
        writer.close()
