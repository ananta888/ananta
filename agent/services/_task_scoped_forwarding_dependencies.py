"""Explicit collaborators of the task-scoped forwarding and step orchestration.

The forwarding and step modules are module-level functions reached through
thin :class:`TaskScopedExecutionService` wrappers. Instead of looking up
patchable names through a facade module at call time, every collaborator they
use is declared here as a small, per-concern port bundle (ISP) and handed to
the functions explicitly (DIP):

- :class:`HubStatePorts`: repositories, core services, task status updater
- :class:`GovernedIndexJobPorts`: the governed knowledge-index job service
- :class:`CodecompassDispatchPorts`: governed dispatch preparation/authorization
  and the execute deadline
- :class:`ForwardOutcomePorts`: the worker-forward outcome recorder
- :class:`KnowledgeIndexRetryPolicy`: exact-replay bounds and the sleep clock
- :class:`ForwardedResultPorts`: Hub-owned result acceptors/normalizers
- :class:`StepOrchestrationPorts`: dispatch admission and admitted execution

:class:`TaskScopedForwardingDependencies` composes them. Entry points take a
keyword-only ``dependencies`` argument; when it is omitted they use
:func:`current_task_scoped_forwarding_dependencies`, which returns the
production bundle unless the documented override seam
:func:`override_task_scoped_forwarding_dependencies` is active (tests).

Production defaults are resolved lazily in :meth:`production` so this module
imports none of the modules that consume it (no import cycle).
"""

from __future__ import annotations

import contextlib
import dataclasses
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator


@dataclass(frozen=True)
class HubStatePorts:
    """Hub persistence and core-service access used while admitting results."""

    repositories: Callable[[], Any]
    core_services: Callable[[], Any]
    update_task_status: Callable[..., Any]


@dataclass(frozen=True)
class GovernedIndexJobPorts:
    """The Source-Control governed knowledge-index job service."""

    governed_index_job_service: Callable[[], Any]


@dataclass(frozen=True)
class CodecompassDispatchPorts:
    """Governed CodeCompass dispatch preparation and its transport deadline.

    ``prepare_worker_dispatch`` receives the ``authorize`` callable to use;
    ``authorize_worker_dispatch`` receives the ``index_job_service`` provider.
    """

    prepare_worker_dispatch: Callable[..., None]
    authorize_worker_dispatch: Callable[..., None]
    execute_deadline: Callable[..., Any]


@dataclass(frozen=True)
class ForwardOutcomePorts:
    """Worker health observation for forwarded requests."""

    outcome_recorder: Callable[[], Any]


@dataclass(frozen=True)
class KnowledgeIndexRetryPolicy:
    """Bounds of the exact governed-v2 execute replay."""

    max_forward_attempts: int = 16
    pending_poll_seconds: float = 0.25
    sleep: Callable[[float], None] = time.sleep


@dataclass(frozen=True)
class ForwardedResultPorts:
    """Hub-owned acceptors and normalizers of forwarded worker results."""

    visual_process_assistant_service: Callable[[], Any]
    normalize_artifacts: Callable[..., Any]
    normalize_recovery_artifacts: Callable[..., Any]


@dataclass(frozen=True)
class StepOrchestrationPorts:
    """Dispatch admission and the admitted execute runner of one step."""

    admit_dispatch: Callable[..., dict]
    run_execute_admitted: Callable[..., Any]


@dataclass(frozen=True)
class TaskScopedForwardingDependencies:
    """Composition of every per-concern port bundle of this subsystem."""

    hub_state: HubStatePorts
    index_jobs: GovernedIndexJobPorts
    codecompass: CodecompassDispatchPorts
    outcomes: ForwardOutcomePorts
    knowledge_index_retry: KnowledgeIndexRetryPolicy
    results: ForwardedResultPorts
    steps: StepOrchestrationPorts

    @classmethod
    def production(cls) -> "TaskScopedForwardingDependencies":
        from agent.services._task_scoped_codecompass_dispatch import (
            _authorize_codecompass_worker_dispatch,
            _codecompass_execute_deadline,
            _governed_source_control_index_job_service,
            _prepare_codecompass_worker_dispatch,
        )
        from agent.services._task_scoped_dispatch_admission import _admit_task_scoped_dispatch
        from agent.services._task_scoped_execute_step import _run_execute_step_admitted
        from agent.services._task_scoped_forwarded_result_acceptance import (
            _get_visual_process_assistant_service,
        )
        from agent.services.forwarded_artifact_normalization import (
            normalize_forwarded_artifacts,
            normalize_recovery_forwarded_artifacts,
        )
        from agent.services.repository_registry import get_repository_registry
        from agent.services.service_registry import get_core_services
        from agent.services.task_runtime_service import update_local_task_status
        from agent.services.worker_forward_outcome import get_worker_forward_outcome_recorder

        return cls(
            hub_state=HubStatePorts(
                repositories=get_repository_registry,
                core_services=get_core_services,
                update_task_status=update_local_task_status,
            ),
            index_jobs=GovernedIndexJobPorts(
                governed_index_job_service=_governed_source_control_index_job_service,
            ),
            codecompass=CodecompassDispatchPorts(
                prepare_worker_dispatch=_prepare_codecompass_worker_dispatch,
                authorize_worker_dispatch=_authorize_codecompass_worker_dispatch,
                execute_deadline=_codecompass_execute_deadline,
            ),
            outcomes=ForwardOutcomePorts(
                outcome_recorder=get_worker_forward_outcome_recorder,
            ),
            knowledge_index_retry=KnowledgeIndexRetryPolicy(),
            results=ForwardedResultPorts(
                visual_process_assistant_service=_get_visual_process_assistant_service,
                normalize_artifacts=normalize_forwarded_artifacts,
                normalize_recovery_artifacts=normalize_recovery_forwarded_artifacts,
            ),
            steps=StepOrchestrationPorts(
                admit_dispatch=_admit_task_scoped_dispatch,
                run_execute_admitted=_run_execute_step_admitted,
            ),
        )

    def with_changes(self, **changes: Any) -> "TaskScopedForwardingDependencies":
        """Return a copy with leaf ports replaced by their unique field name.

        ``with_changes(repositories=...)`` replaces ``hub_state.repositories``;
        a whole bundle can be replaced by its own name (``steps=...``).
        Unknown names raise ``TypeError`` so a stale override fails loudly.
        """

        bundle_changes: dict[str, Any] = {}
        leaf_changes: dict[str, dict[str, Any]] = {}
        bundle_names = {field.name for field in dataclasses.fields(self)}
        for name, value in changes.items():
            if name in bundle_names:
                bundle_changes[name] = value
                continue
            owner = next(
                (
                    bundle_name
                    for bundle_name in bundle_names
                    if name
                    in {
                        field.name
                        for field in dataclasses.fields(getattr(self, bundle_name))
                    }
                ),
                None,
            )
            if owner is None:
                raise TypeError(f"unknown task-scoped forwarding dependency: {name}")
            leaf_changes.setdefault(owner, {})[name] = value
        for owner, values in leaf_changes.items():
            base = bundle_changes.get(owner, getattr(self, owner))
            bundle_changes[owner] = dataclasses.replace(base, **values)
        return dataclasses.replace(self, **bundle_changes)


_override_lock = threading.Lock()
_override: TaskScopedForwardingDependencies | None = None


def current_task_scoped_forwarding_dependencies() -> TaskScopedForwardingDependencies:
    """Return the active bundle: the override when set, else production."""

    active = _override
    if active is not None:
        return active
    return TaskScopedForwardingDependencies.production()


@contextlib.contextmanager
def override_task_scoped_forwarding_dependencies(
    **changes: Any,
) -> Iterator[TaskScopedForwardingDependencies]:
    """Documented override seam: replace ports for the enclosed scope.

    Overrides stack; leaving the scope restores the previous bundle. Intended
    for tests and composition roots, never for per-request behaviour.
    """

    global _override
    with _override_lock:
        previous = _override
        _override = current_task_scoped_forwarding_dependencies().with_changes(**changes)
        installed = _override
    try:
        yield installed
    finally:
        with _override_lock:
            _override = previous


__all__ = [
    "CodecompassDispatchPorts",
    "ForwardOutcomePorts",
    "ForwardedResultPorts",
    "GovernedIndexJobPorts",
    "HubStatePorts",
    "KnowledgeIndexRetryPolicy",
    "StepOrchestrationPorts",
    "TaskScopedForwardingDependencies",
    "current_task_scoped_forwarding_dependencies",
    "override_task_scoped_forwarding_dependencies",
]
