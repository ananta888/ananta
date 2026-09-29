"""Per-dispatch collaborators shared by the autopilot task dispatch phases.

The dispatch of one task runs through several phases (gates, execution-scope
allocation, proposal strategies, strategy exhaustion). Each phase receives this
immutable parameter object instead of a dozen positional collaborators. Its
``dependencies`` bundle (:class:`.autopilot_dispatch_dependencies.DispatchDependencies`)
carries the services and policies the phases delegate to; ``recovery_gate`` is
the gate instance the bundle produced for this dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .autopilot_dispatch_dependencies import DispatchDependencies
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
    dependencies: DispatchDependencies
    log: Any

