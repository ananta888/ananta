"""Task-bound capability for the Hub Recovery source finalizer."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_AUTHORIZED_TASK_ID: ContextVar[str | None] = ContextVar(
    "ananta_recovery_source_finalization_write_task_id",
    default=None,
)


@contextmanager
def authorize_recovery_source_finalization_write(
    task_id: str,
) -> Iterator[None]:
    """Authorize one call chain to publish one source aggregate."""

    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        raise ValueError(
            "recovery_source_finalization_task_id_required"
        )
    token = _AUTHORIZED_TASK_ID.set(normalized_task_id)
    try:
        yield
    finally:
        _AUTHORIZED_TASK_ID.reset(token)


def recovery_source_finalization_write_authorized(
    task_id: str,
) -> bool:
    return bool(
        str(task_id or "").strip()
        and _AUTHORIZED_TASK_ID.get()
        == str(task_id or "").strip()
    )


__all__ = [
    "authorize_recovery_source_finalization_write",
    "recovery_source_finalization_write_authorized",
]
