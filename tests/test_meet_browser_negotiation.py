"""Immutable opt-in is not a navigation grant; old assignments stay unchanged."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from agent.models.meet_dialog_phase import phase_binding
from agent.models.meet_preauthorization_binding import assignment_projection
from agent.services.meet_contract import MeetError
from ananta_contracts.meet_dialog import validate_assignment
from tests.test_meet_dialog_avatar_negotiation import system


@pytest.mark.parametrize("enabled", [False, True])
def test_explicit_option_is_persisted_and_bound_before_dispatch_without_browser_execution(app, enabled):
    with app.app_context():
        f = system()
        f.service.browser_workspaces = Mock()
        f.f.authority.policies[("tenant", "project")] |= {"screen.publish"}
        payload = f.payload | {"capabilities": ["screen.publish"]} | ({"browser_workspace": True} if enabled else {})
        started = f.service.start(f.principal, "project", payload)
        wire = f.worker.start_dialog.call_args.args[0]
        assert validate_assignment(wire, f.f.now) is wire
        assert (wire.get("browser_workspace") is True) == enabled
        task = f.tasks.get_by_id(started["task_id"])
        context = task.worker_execution_context["meet_dialog"]
        current = f.f.authority.current(task.id, context["lease_id"], context["runtime_id"])
        assert current.browser_workspace == enabled
        assert (f.service.inspect(f.principal, "project", task.id).get("browser_workspace") is True) == enabled
        f.service.browser_workspaces.assert_not_called()
        assert not f.service.browser_workspaces.mock_calls
        baseline = deepcopy(context)
        baseline.pop("browser_workspace", None)
        assert (
            phase_binding(task.id, "tenant", "project", context)
            != phase_binding(task.id, "tenant", "project", baseline)
        ) == enabled
        # Compatibility fixture has an empty parent; original preauthorization
        # requires a bound parent, so use a deterministic parent for digest check.
        context = context | {"binding_task_id": "synthetic-parent"}
        original = assignment_projection(task.id, "tenant", "project", current.origin, context)
        assert (original.get("browser_workspace") is True) == enabled


@pytest.mark.parametrize("flag", [False, 1, None, "true"])
def test_malformed_option_never_creates_or_dispatches_task(app, flag):
    with app.app_context():
        f = system()
        f.service.browser_workspaces = Mock()
        f.tasks.start = Mock(wraps=f.tasks.start)
        with pytest.raises(MeetError, match="browser_workspace_invalid"):
            f.service.start(f.principal, "project", f.payload | {"browser_workspace": flag})
        f.tasks.start.assert_not_called()
        f.worker.start_dialog.assert_not_called()


def test_browser_option_requires_screen_capability_and_installed_coordinator(app):
    with app.app_context():
        f = system()
        with pytest.raises(MeetError, match="browser_workspace_invalid"):
            f.service.start(f.principal, "project", f.payload | {"browser_workspace": True})
        f.f.authority.policies[("tenant", "project")] |= {"screen.publish"}
        with pytest.raises(MeetError, match="browser_workspace_unavailable"):
            f.service.start(
                f.principal, "project", f.payload | {"browser_workspace": True, "capabilities": ["screen.publish"]}
            )
        f.worker.start_dialog.assert_not_called()


@pytest.mark.parametrize("changed", [None, False, 1])
def test_actual_task_repository_rejects_retrofit_or_option_removal(app, changed):
    from agent.services.repository_registry import get_repository_registry

    with app.app_context():
        f = system()
        f.service.browser_workspaces = Mock()
        f.f.authority.policies[("tenant", "project")] |= {"screen.publish"}
        started = f.service.start(
            f.principal, "project", f.payload | {"browser_workspace": True, "capabilities": ["screen.publish"]}
        )
        task = f.tasks.get_by_id(started["task_id"])
        if changed is None:
            task.worker_execution_context["meet_dialog"].pop("browser_workspace")
        else:
            task.worker_execution_context["meet_dialog"]["browser_workspace"] = changed
        with pytest.raises(ValueError, match="browser_negotiation_immutable"):
            get_repository_registry().task_repo.save(task)
