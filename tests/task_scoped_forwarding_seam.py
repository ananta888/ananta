"""Test seam for the task-scoped forwarding and step-orchestration ports.

Tests inject doubles explicitly instead of monkeypatching module-level names.
Import the fixture into a test module::

    from tests.task_scoped_forwarding_seam import forwarding_dependencies  # noqa: F401

``forwarding_dependencies(repositories=lambda: repos, ...)`` returns the
accumulated :class:`TaskScopedForwardingDependencies` bundle (every call
stacks on the previous one). Pass it to ``TaskScopedExecutionService(
forwarding_dependencies=...)`` or to an entry point's ``dependencies=``.
When the test uses the ``app`` fixture, the bundle is also installed on that
application (``install_task_scoped_forwarding_dependencies``), so request and
autopilot flows of that app receive it; teardown removes it.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator

import pytest

from agent.services._task_scoped_forwarding_dependencies import (
    TaskScopedForwardingDependencies,
    install_task_scoped_forwarding_dependencies,
)


@pytest.fixture
def forwarding_dependencies(request) -> Iterator[Callable[..., TaskScopedForwardingDependencies]]:
    app = request.getfixturevalue("app") if "app" in request.fixturenames else None
    state: dict[str, TaskScopedForwardingDependencies] = {
        "bundle": TaskScopedForwardingDependencies.production(),
    }

    def override(**changes: Any) -> TaskScopedForwardingDependencies:
        state["bundle"] = state["bundle"].with_changes(**changes)
        if app is not None:
            install_task_scoped_forwarding_dependencies(app, state["bundle"])
        return state["bundle"]

    yield override
    if app is not None:
        install_task_scoped_forwarding_dependencies(app, None)
