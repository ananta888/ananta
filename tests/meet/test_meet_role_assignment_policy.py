"""Closed assignment policy has no SQL/transport dependencies or legacy rights fallback."""

from dataclasses import FrozenInstanceError, replace
from unittest.mock import Mock

import pytest

from agent.models.meet_role_assignment import MeetRoleFacts, bounded_json, publisher_origin
from agent.services.meet_contract import MeetError
from agent.services.meet_role_assignment import MeetRoleAssignments


def facts():
    return MeetRoleFacts(
        "assignment",
        "http://publisher:8091",
        "active",
        1000.0,
        None,
        True,
        "online",
        "worker",
        '["meet_dialog_session"]',
        "{}",
        bounded_json(
            {
                "principal_kinds": ["agent"],
                "required_capabilities": [],
                "forbidden_capabilities": [],
                "write_access_required": False,
            }
        ),
        1,
        "a" * 64,
        "b" * 64,
    )


@pytest.mark.parametrize(
    "change",
    [
        {"registration_validated": 1},
        {"registration_validated": "true"},
        {"role": "hub"},
        {"status": "ONLINE"},
        {"assigned_at": True},
        {"assigned_at": float("nan")},
        {"assigned_at": float("inf")},
        {"assigned_at": 0},
        {"ended_at": 1000},
        {"assignment_id": []},
        {"assignment_id": " "},
        {"authorized_capabilities_json": "{}"},
        {"authorized_capabilities_json": '["meet_dialog_session","meet_dialog_session"]'},
        {"authorized_capabilities_json": "[1]"},
        {"slot_policy_json": "[]"},
        {"execution_limits_json": "[]"},
        *(
            {"slot_policy_json": bounded_json(policy)}
            for policy in [
                {},
                {"principal_kinds": "agent"},
                {"principal_kinds": ["user"]},
                {"principal_kinds": ["agent"], "write_access_required": "true"},
                {"principal_kinds": ["agent"], "required_capabilities": "meet_dialog_session"},
                {"principal_kinds": ["agent"], "forbidden_capabilities": ["meet_dialog_session"]},
            ]
        ),
    ],
)
def test_malformed_or_ineligible_facts_are_not_coerced(change):
    with pytest.raises((ValueError, TypeError)):
        replace(facts(), **change).digest()


def test_immutable_facts_and_exact_changed_authorization_digest():
    value = facts()
    assert len(value.digest()) == 64
    with pytest.raises(FrozenInstanceError):
        value.lifecycle = "ended"
    assert replace(value, assigned_at=1001).digest() != value.digest()
    policy = bounded_json(
        {
            "principal_kinds": ["agent"],
            "required_capabilities": [],
            "forbidden_capabilities": [],
            "write_access_required": True,
        }
    )
    assert replace(value, slot_policy_json=policy, execution_limits_json='{"write_access":true}').digest()
    with pytest.raises(ValueError):
        replace(value, slot_policy_json=policy, execution_limits_json='{"write_access":1}').digest()


@pytest.mark.parametrize(
    "value",
    [
        None,
        1,
        "http://publisher",
        "https://publisher:80",
        "http://publisher:80/",
        "http://user:pass@publisher:80",
        "http://publisher:80?x",
        "http://publisher:80#x",
        "http://publisher:80\n",
        "http://publisher:bad",
        "http://publisher:80/v1/turns",
    ],
)
def test_only_exact_operator_transport_origin_is_an_identity(value):
    with pytest.raises(ValueError):
        publisher_origin(value)


def test_missing_role_does_not_read_directory_but_provider_failure_is_bounded():
    rows = Mock()
    service = MeetRoleAssignments(rows)
    assert service.admit("task", "tenant", "project", {}, {}, None) is None
    rows.read.assert_not_called()
    rows.read.side_effect = RuntimeError("private provider detail")
    scope = {"organization_id": "org", "unit_id": "unit", "role_slot_id": "role"}
    with pytest.raises(MeetError, match="^meet_dialog_assignment_denied$"):
        service.admit("task", "tenant", "project", {}, scope, facts().publisher_url)


def test_metadata_budget_never_accepts_nonfinite_or_large_json():
    for value in (float("nan"), "x" * 16385):
        with pytest.raises(ValueError):
            bounded_json(value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("principal_kinds", ["agent", "unrecognized"]),
        ("write_access_required", 1),
        ("required_capabilities", ["missing"]),
        ("forbidden_capabilities", ["meet_dialog_session"]),
        ("principal_kinds", []),
        ("principal_kinds", ["agent", "agent"]),
        ("unknown", True),
    ],
)
def test_closed_complete_policy_cannot_be_broadened(field, value):
    import json

    valid = facts()
    policy = json.loads(valid.slot_policy_json) | {field: value}
    with pytest.raises(ValueError):
        replace(valid, slot_policy_json=bounded_json(policy)).digest()


@pytest.mark.parametrize("field", ["max_concurrent_tasks", "max_assignments"])
@pytest.mark.parametrize("value", [0, -1, True, "1", 1.5, None])
def test_disabled_or_malformed_registered_execution_capacity_cannot_admit_a_role(field, value):
    with pytest.raises(ValueError):
        replace(facts(), execution_limits_json=bounded_json({field: value})).digest()
