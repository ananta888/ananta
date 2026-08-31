"""Process-local transaction serialization for ML-Intern training repositories."""

from __future__ import annotations

import threading
from functools import wraps
from typing import Callable, ParamSpec, TypeVar

from sqlalchemy.exc import IntegrityError

_P = ParamSpec("_P")
_R = TypeVar("_R")
_REPOSITORY_WRITE_LOCK = threading.RLock()


def serialized_write(callback: Callable[_P, _R]) -> Callable[_P, _R]:
    """Prevent transaction overlap on one process-local SQLite connection."""

    @wraps(callback)
    def guarded(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with _REPOSITORY_WRITE_LOCK:
            return callback(*args, **kwargs)

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
