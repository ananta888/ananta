"""Synthetic Hub lifecycle checks; no Meet identity or production evidence."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_lifecycle import MeetDialogLifecycle, organization_tuple
from tests.test_meet_dialog_authority import fixture


def test_cancelled_parent_cannot_authorize_a_live_dialog_or_read_its_room():
    f = fixture()
    parent = SimpleNamespace(id="parent", tenant_id="tenant", project_id="project", status="cancelled", archived=False)
    f.context["binding_task_id"] = "parent"
    f.task.parent_task_id = "parent"
    f.tasks.get_by_id.side_effect = lambda key: {"task": f.task, "parent": parent}.get(key)
    with pytest.raises(MeetError, match="^meet_dialog_parent_inactive$"):
        f.authority.current("task", "dispatch", "runtime")
    f.binding.read.assert_not_called()


def parented():
    f = fixture()
    parent = SimpleNamespace(
        id="parent",
        tenant_id="tenant",
        project_id="project",
        status="in_progress",
        archived=False,
        organization_id="org",
        unit_id="unit",
        team_id="team",
        role_slot_id="slot",
    )
    f.context["binding_task_id"] = "parent"
    f.task.parent_task_id = "parent"
    for key, value in organization_tuple(parent).items():
        setattr(f.task, key, value)
    f.tasks.get_by_id.side_effect = lambda key: {"task": f.task, "parent": parent}.get(key)
    gate = Mock()
    gate.evaluate.return_value = SimpleNamespace(allowed=True)
    # This unit fixture isolates parent/topology policy; real assignment checks
    # are exercised with SQL registration in test_meet_role_assignment_lifecycle.
    f.authority.lifecycle = MeetDialogLifecycle(f.tasks, gate, role_assignments=Mock())
    return f, parent, gate


@pytest.mark.parametrize(
    "status", ["completed", "failed", "cancelled", "archived", "verification_failed", "unknown", None, []]
)
def test_no_terminal_or_unknown_parent_state_can_refresh_authority(status):
    f, parent, gate = parented()
    parent.status = status
    with pytest.raises(MeetError, match="^meet_dialog_parent_inactive$"):
        f.authority.current("task", "dispatch", "runtime")
    gate.evaluate.assert_not_called()
    f.binding.read.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [("id", "other"), ("tenant_id", "other"), ("project_id", "other"), ("archived", True), ("archived", 0)],
)
def test_parent_identity_and_archive_state_are_not_coerced(field, value):
    f, parent, _ = parented()
    setattr(parent, field, value)
    with pytest.raises(MeetError, match="^meet_dialog_parent_inactive$"):
        f.authority.current("task", "dispatch", "runtime")


@pytest.mark.parametrize("field", ["organization_id", "unit_id", "team_id", "role_slot_id"])
def test_scope_drift_or_missing_legacy_inheritance_is_never_repaired(field):
    f, parent, gate = parented()
    f.authority.current("task", "dispatch", "runtime")
    setattr(parent, field, "replacement")
    with pytest.raises(MeetError, match="^meet_dialog_parent_scope_changed$"):
        f.authority.current("task", "dispatch", "runtime")
    assert getattr(f.task, field) != "replacement"
    assert gate.evaluate.call_count == 2


@pytest.mark.parametrize("value", [0, False, [], {}, " leading", "x" * 192])
def test_malformed_organization_fields_are_not_dropped_or_stringified(value):
    f, _, gate = parented()
    f.task.organization_id = value
    with pytest.raises(MeetError, match="^meet_dialog_organization_scope_invalid$"):
        f.authority.current("task", "dispatch", "runtime")
    gate.evaluate.assert_not_called()


@pytest.mark.parametrize("error", [RuntimeError("private_provider_detail"), MeetError("private_provider_detail", 403)])
def test_every_refresh_rechecks_organization_and_hides_provider_failure_details(error):
    f, _, gate = parented()
    f.authority.current("task", "dispatch", "runtime")
    gate.evaluate.return_value.allowed = False
    with pytest.raises(MeetError, match="^meet_dialog_organization_inactive$"):
        f.authority.current("task", "dispatch", "runtime")
    gate.evaluate.side_effect = error
    with pytest.raises(MeetError, match="^meet_dialog_lifecycle_unavailable$"):
        f.authority.current("task", "dispatch", "runtime")


def test_missing_parent_or_uncertain_lookup_fails_closed():
    f, _, _ = parented()
    f.tasks.get_by_id.side_effect = lambda key: f.task if key == "task" else None
    with pytest.raises(MeetError, match="^meet_dialog_parent_inactive$"):
        f.authority.current("task", "dispatch", "runtime")
    f.tasks.get_by_id.side_effect = RuntimeError("private_storage_detail")
    with pytest.raises(MeetError, match="^meet_dialog_lifecycle_unavailable$"):
        f.authority.lifecycle.scope_for_parent("tenant", "project", "parent")


def test_parent_link_cannot_be_replaced_independently_of_the_closed_session_context():
    f, _, gate = parented()
    f.task.parent_task_id = "other"
    with pytest.raises(MeetError, match="^meet_dialog_parent_changed$"):
        f.authority.current("task", "dispatch", "runtime")
    gate.evaluate.assert_not_called()


def test_a_session_cannot_use_itself_as_parent_authority():
    f, _, gate = parented()
    f.task.id = "parent"
    with pytest.raises(MeetError, match="^meet_dialog_parent_changed$"):
        f.authority.current("task", "dispatch", "runtime")
    gate.evaluate.assert_not_called()


def test_standalone_project_and_legacy_team_sessions_need_no_organization_provider():
    f = fixture()
    f.task.team_id = "legacy-team"
    gate = Mock(side_effect=AssertionError("must not consult organization policy"))
    f.authority.lifecycle = MeetDialogLifecycle(f.tasks, gate)
    assert f.authority.current("task", "dispatch", "runtime").project_id == "project"
    gate.evaluate.assert_not_called()
