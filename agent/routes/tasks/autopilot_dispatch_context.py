"""Per-dispatch collaborators shared by the autopilot task dispatch phases.

The dispatch of one task runs through several phases (gates, execution-scope
allocation, proposal strategies, strategy exhaustion). Each phase receives this
immutable parameter object instead of a dozen positional collaborators.
Collaborators that tests monkeypatch on
:mod:`agent.routes.tasks.autopilot_task_dispatcher` (the recovery gate service
and ``_current_task_status``) are resolved by the dispatcher at call time and
injected here, so the phases never import them directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .autopilot_task_dispatcher_helpers import TaskDispatchResult


@dataclass(frozen=True)
class DispatchContext:
    task: Any
    target_worker: Any
    loop: Any
    services: Any
    app_ctx: Any
    result: TaskDispatchResult
    recovery_gate: Any
    append_trace_event: Callable[..., None]
    update_local_task_status: Callable[..., None]
    current_task_status: Callable[..., Any]
    log: Any
