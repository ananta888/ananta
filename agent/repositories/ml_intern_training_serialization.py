"""Process-local transaction serialization for ML-Intern training repositories."""

from __future__ import annotations

import threading
import time
from functools import wraps
from typing import Callable, ParamSpec, TypeVar

from sqlalchemy.exc import IntegrityError, OperationalError

_P = ParamSpec("_P")
_R = TypeVar("_R")
_REPOSITORY_WRITE_LOCK = threading.RLock()
# A write that meets a transient SQLite lock ("database is locked" after the busy timeout, or a
# shared-cache "database table is locked") is retried: it committed nothing, so it is repeatable.
_SQLITE_LOCK_RETRIES = 5
_SQLITE_LOCK_BACKOFF_SECONDS = 0.05


def _is_transient_sqlite_lock(exc: OperationalError) -> bool:
    return "locked" in str(getattr(exc, "orig", exc)).lower()


def serialized_write(callback: Callable[_P, _R]) -> Callable[_P, _R]:
    """Prevent transaction overlap on one process-local SQLite connection."""

    @wraps(callback)
    def guarded(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        for attempt in range(_SQLITE_LOCK_RETRIES + 1):
            with _REPOSITORY_WRITE_LOCK:
                try:
                    return callback(*args, **kwargs)
                except OperationalError as exc:
                    if attempt >= _SQLITE_LOCK_RETRIES or not _is_transient_sqlite_lock(exc):
                        raise
            time.sleep(_SQLITE_LOCK_BACKOFF_SECONDS * (attempt + 1))  # outside the lock: let the holder finish
        raise AssertionError("unreachable")

    return guarded


def serialized_sqlite_read(callback: Callable[_P, _R]) -> Callable[_P, _R]:
    """Fence SQLite reads against process-local repository writes."""

    @wraps(callback)
    def guarded(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        repository = args[0]
        dialect = getattr(getattr(repository, "_engine", None), "dialect", None)
        if getattr(dialect, "name", None) != "sqlite":
            return callback(*args, **kwargs)
        with _REPOSITORY_WRITE_LOCK:
            return callback(*args, **kwargs)

    return guarded


def is_slot_or_idempotency_conflict(exc: IntegrityError) -> bool:
    """Return whether a unique conflict represents a taken training slot."""

    text = str(getattr(exc, "orig", exc)).lower()
    if "foreign key" in text:
        return False
    return "unique" in text or "duplicate" in text
