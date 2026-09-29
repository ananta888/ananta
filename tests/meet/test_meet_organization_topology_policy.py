"""Closed topology decisions without SQL, transport or assignment invention."""

from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.models.meet_organization_topology import MeetTopologyScope, MeetTopologySnapshot
from agent.services.meet_organization_topology import MeetOrganizationTopologyGate, active_topology

SCOPE = MeetTopologyScope("tenant", "project", "org", "unit", "team", "slot")
ACTIVE = MeetTopologySnapshot("active", "active", "unit", True, "active", "unit")


@pytest.mark.parametrize("field", ["unit_lifecycle", "team_lifecycle", "role_lifecycle"])
@pytest.mark.parametrize("status", [None, "planned", "draining", "archived", "unknown", True])
def test_every_nonactive_referenced_leaf_is_denied(field, status):
    assert not active_topology(SCOPE, replace(ACTIVE, **{field: status}))


@pytest.mark.parametrize("active", [False, None, 1, "true", [], {}])
def test_team_active_flag_is_never_coerced(active):
    assert not active_topology(SCOPE, replace(ACTIVE, team_active=active))


@pytest.mark.parametrize("field", ["team_unit_id", "role_unit_id"])
@pytest.mark.parametrize("unit", [None, "", "another-unit"])
def test_role_and_team_must_belong_to_exact_task_unit(field, unit):
    assert not active_topology(SCOPE, replace(ACTIVE, **{field: unit}))


def test_leaves_cannot_be_bound_without_their_unit_and_snapshots_are_immutable():
    assert not active_topology(replace(SCOPE, unit_id=""), ACTIVE)
    assert not active_topology(SCOPE, {})
    assert active_topology(SCOPE, ACTIVE)
    assert active_topology(replace(SCOPE, team_id="", role_slot_id=""), MeetTopologySnapshot(unit_lifecycle="active"))
    with pytest.raises(FrozenInstanceError):
        ACTIVE.team_active = False


@pytest.mark.parametrize(
    "scope",
    [
        dict(tenant_id="tenant", project_id="project"),
        dict(tenant_id="tenant", project_id="project", organization_id="org"),
    ],
)
def test_standalone_and_organization_only_tasks_need_no_leaf_database(scope):
    org, rows = Mock(), Mock()
    org.evaluate.return_value.allowed = True
    rows.read.side_effect = AssertionError("no leaf lookup needed")
    assert MeetOrganizationTopologyGate(org, rows).evaluate(SimpleNamespace(**scope)).allowed
    org.evaluate.assert_called_once()
    rows.read.assert_not_called()


@pytest.mark.parametrize(
    "patch",
    [
        {"tenant_id": None},
        {"project_id": "../foreign"},
        {"unit_id": False},
        {"role_slot_id": " leading"},
        {"organization_id": 1},
    ],
)
def test_malformed_scope_is_denied_before_either_provider(patch):
    org, rows = Mock(), Mock()
    fields = vars(SCOPE) | patch
    result = MeetOrganizationTopologyGate(org, rows).evaluate(SimpleNamespace(**fields))
    assert not result.allowed and result.reason_code == "meet_organization_scope_invalid"
    org.evaluate.assert_not_called()
    rows.read.assert_not_called()


def test_parent_organization_denial_precedes_leaf_reads_and_provider_failure_is_content_free():
    org, rows = Mock(), Mock()
    org.evaluate.return_value.allowed = False
    gate = MeetOrganizationTopologyGate(org, rows)
    task = SimpleNamespace(**vars(SCOPE))
    assert not gate.evaluate(task).allowed
    rows.read.assert_not_called()
    org.evaluate.return_value.allowed = True
    rows.read.side_effect = RuntimeError("private_scope_or_storage_detail")
    with pytest.raises(ValueError, match="^meet_organization_topology_unavailable$"):
        gate.evaluate(task)
