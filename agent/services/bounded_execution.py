"""Wall-clock bounds for request handlers whose work may outlive their socket.

A propose whose worker threads die mid-flight (restarts, severed connections)
lingered unbounded and held the hub's dispatch slot; the response must be
bounded even when the underlying work is not. A zombie thread may outlive the
bound — its own inner timeouts and the kernel's TCP bounds end it — but the
request completes deterministically and the dispatch slot is released.
"""

from __future__ import annotations

import concurrent.futures
import contextvars
import logging
from typing import Callable, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

# Just under the hub's dispatch hard timeout (propose + execute + 30s slack at
# the governed policy levels): the worker always answers before the hub gives up.
WALL_TIMEOUT_SECONDS = 2100


class WallTimeout(RuntimeError):
    """The bounded call exceeded its wall-clock budget."""


def run_wall_bounded(fn: Callable[[], T], *, timeout_seconds: float, label: str) -> T:
    """Run ``fn`` with a hard wall-clock bound and return its result.

    The caller's contextvars ride along: capability scopes and request context
    live in the context, and a bare thread would silently lose them (observed as
    empty analysis observations). On timeout the executor is abandoned
    (``wait=False``) and ``WallTimeout`` is raised: callers map it to a bounded
    error response instead of a hung socket.
    """
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix=label)
    try:
        return pool.submit(contextvars.copy_context().run, fn).result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError as exc:
        pool.shutdown(wait=False)
        log.warning("wall_bounded_timeout label=%s timeout_seconds=%s", label, timeout_seconds)
        raise WallTimeout(f"{label} exceeded {timeout_seconds}s") from exc
