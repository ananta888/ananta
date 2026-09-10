"""Task transaction ownership shared with workflow authority stores."""

from collections.abc import Iterator
from contextlib import contextmanager

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlmodel import Session

from agent.services.workflow_runtime.sqlalchemy_support import sqlite_transaction_guard


@contextmanager
def task_repository_session(engine: Engine, *, write: bool = False) -> Iterator[Session]:
    """Keep SQLite reads/writes off another store's shared connection transaction.

    For writes, acquire the database writer before authoritative reads. The
    caller retains its existing commit/rollback and PostgreSQL row-lock logic.
    """
    with sqlite_transaction_guard(engine), Session(engine) as session:
        if write and engine.dialect.name == "sqlite":
            session.execute(sa.text("BEGIN IMMEDIATE"))
        yield session
