"""Task-lookup helpers for the task-scoped execution service.

Extracted from ``agent.services.task_scoped_execution_service`` as part of
SPLIT-001 (sub-split 001s). The module owns the small, well-isolated
helpers that resolve a task id to a task dict, optionally falling back
to a hub sync.

There are two operations:

* :func:`require_task` returns the task dict for ``tid`` or raises
  :class:`TaskNotFoundError`. If the local task store is empty, it
  delegates to :func:`maybe_sync_task_from_hub` to fetch a fresh copy
  from the hub.
* :func:`maybe_sync_task_from_hub` performs the hub-side sync, gated by
  the ``execution_fallback_policy.worker_task_sync_from_hub_enabled``
  flag in the agent config (default enabled).

Backwards compatibility is preserved at the service boundary via thin
delegating wrappers in :class:`TaskScopedExecutionService` (12-month
deprecation window, see todos/todo.refactor-large-files-split.json
SPLIT-001).
"""

from __future__ import annotations

from typing import Any

from flask import current_app, has_app_context

from agent.common.errors import TaskNotFoundError
from agent.services.task_runtime_service import get_local_task_status


def maybe_sync_task_from_hub(tid: str) -> dict | None:
    """Sync ``tid`` from the hub, gated by the agent config flag.

    Returns the synced task dict, or ``None`` if the gate is closed or
    the hub has no record for ``tid``.
    """
    try:
        agent_cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}) if has_app_context() else {}
    except Exception:
        agent_cfg = {}
    fp = dict((agent_cfg.get("execution_fallback_policy") or {}))
    if not bool(fp.get("worker_task_sync_from_hub_enabled", True)):
        return None
    from agent.services.task_runtime_service import sync_task_from_hub
    return sync_task_from_hub(tid)


_maybe_sync_task_from_hub = maybe_sync_task_from_hub


def require_task(tid: str) -> Any:
    """Return the task dict for ``tid`` or raise :class:`TaskNotFoundError`.

    Falls back to a hub sync if the local store has no entry.
    """
    task = get_local_task_status(tid)
    if not task:
        task = maybe_sync_task_from_hub(tid)
    if not task:
        raise TaskNotFoundError()
    return task


_require_task = require_task
