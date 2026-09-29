"""Test seam for the task-scoped forwarding and step-orchestration ports.

Tests inject doubles through the documented production override seam
``override_task_scoped_forwarding_dependencies`` instead of monkeypatching
module-level names. Import the fixture into a test module::

    from tests.task_scoped_forwarding_seam import forwarding_dependencies  # noqa: F401

and call ``forwarding_dependencies(repositories=lambda: repos, ...)``; every
override stacks on the previous one and is undone at teardown.
"""

from __future__ import annotations

import contextlib
from typing import Any, Callable, Iterator

import pytest

from agent.services._task_scoped_forwarding_dependencies import (
    TaskScopedForwardingDependencies,
    override_task_scoped_forwarding_dependencies,
)


@pytest.fixture
def forwarding_dependencies() -> Iterator[Callable[..., TaskScopedForwardingDependencies]]:
    with contextlib.ExitStack() as stack:

        def override(**changes: Any) -> TaskScopedForwardingDependencies:
            return stack.enter_context(override_task_scoped_forwarding_dependencies(**changes))

        yield override
