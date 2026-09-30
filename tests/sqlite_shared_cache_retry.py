"""Retry statements that the shared-cache test database rejects with "database table is locked".

The default test database is an in-memory SQLite database in shared-cache mode (tests/isolation_database.py).
In that mode table-level locks are not waited for: a statement that meets a lock held by another connection of
the same process (a background executor writing while a request thread reads) fails immediately with
SQLITE_LOCKED ("database table is locked") instead of waiting like busy_timeout would. PostgreSQL in
production has no such behavior, so these failures were test-only flakes (ML-Intern training control,
speech evidence, planning).

A statement rejected with SQLITE_LOCKED made no change, so executing it again is safe. This module registers
a SQLAlchemy ``do_execute``/``do_executemany`` hook for shared-cache SQLite engines only that retries such a
statement a bounded number of times with a short backoff. Anything else (other errors, other databases) is
untouched. ``ANANTA_TEST_SQLITE_LOCK_RETRY=0`` switches it off.
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine

_LOCKED_MESSAGE = "database table is locked"
_ATTEMPTS = 40
_BACKOFF_SECONDS = 0.025
_installed = False


def _is_shared_cache(context: Any) -> bool:
    engine = getattr(context, "engine", None) or getattr(getattr(context, "root_connection", None), "engine", None)
    if engine is None or engine.dialect.name != "sqlite":
        return False
    return "cache=shared" in str(engine.url)


def _run(execute, *args) -> None:
    for attempt in range(_ATTEMPTS):
        try:
            execute(*args)
            return
        except sqlite3.OperationalError as error:
            if _LOCKED_MESSAGE not in str(error) or attempt == _ATTEMPTS - 1:
                raise
            time.sleep(_BACKOFF_SECONDS * (attempt + 1))


def _do_execute(cursor, statement, parameters, context):
    if context is None or not _is_shared_cache(context):
        return None
    _run(cursor.execute, statement, parameters)
    return True


def _do_executemany(cursor, statement, parameters, context):
    if context is None or not _is_shared_cache(context):
        return None
    _run(cursor.executemany, statement, parameters)
    return True


def install() -> None:
    global _installed
    if _installed or os.environ.get("ANANTA_TEST_SQLITE_LOCK_RETRY", "1").strip().lower() in {"0", "false", "no", "off"}:
        return
    event.listen(Engine, "do_execute", _do_execute)
    event.listen(Engine, "do_executemany", _do_executemany)
    _installed = True
