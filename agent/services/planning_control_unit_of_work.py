from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import contextmanager
from threading import RLock
from types import TracebackType
from typing import Iterator

from sqlmodel import Session

from agent.repositories.planning_artifacts import PlanningArtifactRepository

_PLANNING_LOCK_STRIPES = tuple(RLock() for _ in range(64))


@contextmanager
def planning_scope_lock(scope_key: str) -> Iterator[None]:
    """Bounded development-backend replacement for aggregate DB locks."""
    digest = hashlib.sha256(str(scope_key or "").encode("utf-8")).digest()
    lock = _PLANNING_LOCK_STRIPES[digest[0] % len(_PLANNING_LOCK_STRIPES)]
    with lock:
        yield


class PlanningControlUnitOfWork:
    """Own one transaction shared by Planning, Approval and Task writes."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] | None = None,
    ) -> None:
        self._session_factory = session_factory or self._default_session
        self.session: Session | None = None
        self.planning: PlanningArtifactRepository | None = None

    @staticmethod
    def _default_session() -> Session:
        from agent.database import engine

        return Session(engine)

    def __enter__(self) -> PlanningControlUnitOfWork:
        self.session = self._session_factory()
        # Application services return immutable receipt/revision projections
        # after the transaction boundary; keep loaded scalar state available
        # without triggering an accidental post-commit query.
        self.session.expire_on_commit = False
        self.planning = PlanningArtifactRepository(self.session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.session is None:
            return
        try:
            if exc_type is None:
                self.session.commit()
            else:
                self.session.rollback()
        finally:
            self.session.close()
            self.session = None
            self.planning = None


__all__ = ["PlanningControlUnitOfWork", "planning_scope_lock"]
