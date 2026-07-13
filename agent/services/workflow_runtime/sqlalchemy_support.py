"""Small SQLAlchemy infrastructure seam shared by workflow-runtime stores."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

SessionFactory = Callable[[], Session]


def stable_row_id(namespace: str, *parts: object) -> str:
    """Return an opaque deterministic key without leaking tenant identifiers."""

    framed = "\x1f".join([str(namespace), *(str(part) for part in parts)])
    return f"{namespace}-{hashlib.sha256(framed.encode('utf-8')).hexdigest()}"


class SQLAlchemyStoreSupport:
    """Own transaction and dialect handling without importing the global engine.

    Accepting an ``Engine`` or session factory keeps repositories testable and
    avoids coupling the domain package to ``agent.database`` initialization.
    """

    def __init__(self, bind: Engine | SessionFactory) -> None:
        if isinstance(bind, Engine):
            self._session_factory: SessionFactory = sessionmaker(
                bind=bind,
                class_=Session,
                expire_on_commit=False,
            )
            self._dialect_name = bind.dialect.name
        elif callable(bind):
            self._session_factory = bind
            probe = bind()
            try:
                if probe.bind is None:
                    raise ValueError("workflow_runtime_session_bind_required")
                self._dialect_name = probe.bind.dialect.name
            finally:
                probe.close()
        else:
            raise TypeError("workflow_runtime_engine_or_session_factory_required")

        # SQLite has no row-level locks. Serialising one adapter instance gives
        # in-memory SQLite the same observable CAS semantics; database UNIQUE
        # constraints still protect separate processes/adapters.
        self._lock = threading.RLock()

    @contextmanager
    def _transaction(self) -> Iterator[Session]:
        with self._lock, self._session_factory() as session:
            try:
                with session.begin():
                    yield session
            except Exception:
                session.rollback()
                raise

    @contextmanager
    def _read_session(self) -> Iterator[Session]:
        with self._lock, self._session_factory() as session:
            yield session

    def _for_update(self, statement: Select[Any], *, skip_locked: bool = False) -> Select[Any]:
        if self._dialect_name == "postgresql":
            return statement.with_for_update(skip_locked=skip_locked)
        return statement
