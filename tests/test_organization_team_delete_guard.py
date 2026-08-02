from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent.repositories.organization_team_deletion import OrganizationTeamBinding
from agent.services.organization_team_deletion_service import (
    OrganizationTeamDeletionError,
    OrganizationTeamDeletionPrincipal,
    OrganizationTeamDeletionService,
)


class _TeamPort:
    def __init__(self, state):
        self.state = state

    def lock_team(self, team_id):
        return self.state["team"] if self.state["team"] and self.state["team"].id == team_id else None

    def delete(self, _team):
        self.state["team"] = None


class _LinkPort:
    def __init__(self, state):
        self.state = state

    def lock_bindings(self, _team_id):
        return list(self.state["bindings"])


class _Authority:
    def can_manage(self, *, principal, binding):
        return principal.is_hub_admin or (
            principal.principal_id == "org-admin"
            and principal.tenant_id == binding.tenant_id
            and principal.project_id == binding.project_id
        )


class _MemberPort:
    def __init__(self, state):
        self.state = state

    def list_for_team(self, _team_id):
        return list(self.state["members"])

    def delete_all(self, _members):
        self.state["members"] = []


class _AssignmentPort:
    def __init__(self, state, key):
        self.state = state
        self.key = key

    def lock_for_team(self, _team_id):
        return list(self.state[self.key])

    def clear_team(self, rows):
        for row in rows:
            row.team_id = None


class _FakeUow:
    def __init__(self, state):
        self.state = state
        self.teams = _TeamPort(state)
        self.organization_links = _LinkPort(state)
        self.authority = _Authority()
        self.members = _MemberPort(state)
        self.tasks = _AssignmentPort(state, "tasks")
        self.goals = _AssignmentPort(state, "goals")
        self._before = None

    def __enter__(self):
        self._before = deepcopy(self.state)
        return self

    def flush(self):
        return None

    def __exit__(self, exc_type, _exc, _traceback):
        if exc_type is not None:
            self.state.clear()
            self.state.update(self._before)
        return False


def _state(*, bindings=()):
    return {
        "team": SimpleNamespace(id="team-1"),
        "bindings": list(bindings),
        "members": [SimpleNamespace(id="member-1")],
        "tasks": [SimpleNamespace(id="task-1", team_id="team-1", verification_status={}, status_reason_details={})],
        "goals": [SimpleNamespace(id="goal-1", team_id="team-1")],
    }


def _binding():
    return OrganizationTeamBinding(
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        organization_lifecycle="active",
        link_lifecycle="active",
    )


def _service(state, *, fault_injector=None):
    return OrganizationTeamDeletionService(
        uow_factory=lambda: _FakeUow(state),
        fault_injector=fault_injector,
    )


def test_linked_team_conflicts_before_any_clear_for_authorized_admin():
    state = _state(bindings=[_binding()])

    with pytest.raises(OrganizationTeamDeletionError) as caught:
        _service(state).delete(
            team_id="team-1",
            principal=OrganizationTeamDeletionPrincipal(
                principal_id="org-admin",
                tenant_id="tenant-a",
                project_id="project-a",
            ),
        )

    assert caught.value.public_status == 409
    assert caught.value.details == {
        "organization_id": "organization-a",
        "organization_lifecycle": "active",
        "team_link_lifecycle": "active",
        "next_step": "drain_or_migrate_team_via_organization_lifecycle",
    }
    assert state["team"] is not None
    assert state["members"]
    assert state["tasks"][0].team_id == "team-1"
    assert state["goals"][0].team_id == "team-1"


def test_foreign_link_is_non_enumerable_and_leaves_aggregate_unchanged():
    state = _state(bindings=[_binding()])

    with pytest.raises(OrganizationTeamDeletionError) as caught:
        _service(state).delete(
            team_id="team-1",
            principal=OrganizationTeamDeletionPrincipal(
                principal_id="guessed-user",
                tenant_id="tenant-b",
                project_id="project-b",
            ),
        )

    assert caught.value.public_status == 404
    assert caught.value.details == {}
    assert state["team"] is not None
    assert state["members"]


def test_legacy_unlinked_team_is_cleared_and_deleted_atomically():
    state = _state()

    result = _service(state).delete(
        team_id="team-1",
        principal=OrganizationTeamDeletionPrincipal(principal_id="hub-admin", is_hub_admin=True),
    )

    assert result.deleted_members == 1
    assert result.cleared_tasks == 1
    assert result.cleared_goals == 1
    assert state["team"] is None
    assert state["members"] == []
    assert state["tasks"][0].team_id is None
    assert state["goals"][0].team_id is None


@pytest.mark.parametrize(
    "fault_step",
    ["members_deleted", "tasks_cleared", "goals_cleared", "team_deleted"],
)
def test_fault_after_each_planned_write_rolls_back_all_mutations(fault_step):
    state = _state()

    def fail(step):
        if step == fault_step:
            raise RuntimeError(f"injected:{step}")

    with pytest.raises(RuntimeError, match=fault_step):
        _service(state, fault_injector=fail).delete(
            team_id="team-1",
            principal=OrganizationTeamDeletionPrincipal(principal_id="hub-admin", is_hub_admin=True),
        )

    assert state["team"] is not None
    assert state["members"]
    assert state["tasks"][0].team_id == "team-1"
    assert state["goals"][0].team_id == "team-1"


def test_active_recovery_source_blocks_legacy_team_delete_before_clear():
    state = _state()
    state["tasks"][0].verification_status = {"model_recovery": {"plan_id": "plan-1"}}

    with pytest.raises(OrganizationTeamDeletionError) as caught:
        _service(state).delete(
            team_id="team-1",
            principal=OrganizationTeamDeletionPrincipal(principal_id="hub-admin", is_hub_admin=True),
        )

    assert caught.value.public_status == 409
    assert caught.value.reason_code == "recovery_source_mutation_requires_hub_control"
    assert state["team"] is not None
    assert state["members"]
