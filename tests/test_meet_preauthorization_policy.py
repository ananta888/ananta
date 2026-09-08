"""Closed synthetic operator documents and immutable original assignment facts."""

from copy import deepcopy

import pytest

from agent.models.meet_preauthorization_binding import assignment_projection, policy_binding, validate_policy_binding
from agent.models.meet_preauthorization_policy import MeetPreauthorizationPolicy, digest
from agent.services.meet_contract import MeetError


def document():
    return {
        "schema": "ananta.meet-dialog-preauthorization-policy.v1",
        "policy_id": "synthetic-policy",
        "tenant_id": "tenant",
        "project_id": "project",
        "parent_task_id": "parent",
        "owner_subject": "owner",
        "origin": "https://meet.example.test",
        "room_id": "room-" + "a" * 18,
        "capabilities": ["screen.publish"],
        "valid_from": 100,
        "expires_at": 1000,
        "max_duration_seconds": 300,
        "max_dispatches": 2,
    }


def context():
    return {
        "lease_id": "lease",
        "runtime_id": "runtime",
        "session_id": "session",
        "room_id": "room-" + "a" * 18,
        "binding_task_id": "parent",
        "owner_subject": "owner",
        "deadline": 400,
        "capabilities": ["screen.publish"],
        "chat_mode": "off",
        "audio_mode": "off",
    }


def assignment():
    return assignment_projection("task", "tenant", "project", document()["origin"], context())


def test_policy_roundtrip_is_closed_and_assignment_digest_ignores_only_mutable_source_state():
    policy = MeetPreauthorizationPolicy.parse(document())
    assert policy.document() == document()
    policy.require_assignment(assignment(), 100, starting=True)
    policy.require_assignment(assignment(), 200)
    changed = context() | {"controls": {"screen": "synthetic"}, "audio_count": 1}
    assert assignment_projection("task", "tenant", "project", document()["origin"], changed) == assignment()
    binding = policy_binding(policy.policy_id, 1, digest(assignment()))
    assert validate_policy_binding(binding) == binding


@pytest.mark.parametrize(
    "patch",
    [
        {"schema": "approved"},
        {"approval": True},
        {"run_id": "forbidden"},
        {"capabilities": []},
        {"capabilities": ["screen.publish", "screen.publish"]},
        {"capabilities": ["tools.execute"]},
        {"capabilities": [True]},
        {"parent_task_id": ""},
        {"parent_task_id": "*"},
        {"owner_subject": "*"},
        {"tenant_id": ""},
        {"project_id": None},
        {"origin": "http://meet.example.test"},
        {"origin": "https://meet.example.test/path"},
        {"origin": "https://user:secret@meet.example.test"},
        {"room_id": "*"},
        {"valid_from": True},
        {"valid_from": -1},
        {"expires_at": 100},
        {"expires_at": 2**44},
        {"expires_at": 3_000_000},
        {"max_duration_seconds": 29},
        {"max_duration_seconds": 7201},
        {"max_dispatches": True},
        {"max_dispatches": 0},
        {"max_dispatches": 1001},
        {"policy_id": "a" * 161},
    ],
)
def test_unknown_scope_wildcards_authority_fields_and_unbounded_policy_are_rejected(patch):
    with pytest.raises(MeetError):
        MeetPreauthorizationPolicy.parse(document() | patch)


@pytest.mark.parametrize("field", ["tenant_id", "project_id", "parent_task_id", "owner_subject", "origin", "room_id"])
def test_exact_scope_never_inherits_a_neighbour_policy(field):
    with pytest.raises(MeetError, match="scope_denied"):
        MeetPreauthorizationPolicy.parse(document()).require_assignment(assignment() | {field: "foreign"}, 100)


@pytest.mark.parametrize("now", [99, 400, 1000, float("nan"), float("inf"), True])
def test_freshness_and_original_assignment_deadline_remain_authoritative(now):
    with pytest.raises(MeetError):
        MeetPreauthorizationPolicy.parse(document()).require_assignment(assignment(), now)


def test_duration_capability_and_expiry_limits_are_independent():
    policy = MeetPreauthorizationPolicy.parse(document())
    for changed in (assignment() | {"deadline": 401}, assignment() | {"capabilities": ["audio.receive"]}):
        with pytest.raises(MeetError, match="denied"):
            policy.require_assignment(changed, 100, starting=True)
    with pytest.raises(MeetError):
        policy.require_assignment(assignment() | {"deadline": 1001}, 900)


@pytest.mark.parametrize(
    "patch",
    [
        {"schema": "wrong"},
        {"revision": 0},
        {"revision": True},
        {"assignment_digest": "wrong"},
        {"policy_id": "*"},
        {"allow": True},
    ],
)
def test_bound_pointer_never_accepts_authority_or_ambiguous_revision(patch):
    with pytest.raises(MeetError):
        validate_policy_binding(policy_binding("policy", 1, digest(assignment())) | patch)


def test_all_original_dispatch_identity_changes_alter_bound_digest():
    original = context()
    for key in ("lease_id", "runtime_id", "session_id", "binding_task_id", "owner_subject", "deadline"):
        changed = deepcopy(original)
        changed[key] = 401 if key == "deadline" else "other"
        assert digest(assignment_projection("task", "tenant", "project", document()["origin"], changed)) != digest(
            assignment()
        )
