"""Closed additive reconnect negotiation, original deadline and exact endpoint."""

import pytest

from ananta_contracts.meet_dialog import validate_assignment, validate_callback
from ananta_contracts.meet_reconnect import validate_reconnect_response
from tests.test_meet_dialog_transport import assignment


def fixture():
    task = assignment() | {"reconnect": True}
    now = task["deadline"] - 120
    request = {
        "schema": "ananta.meet-dialog-callback.v1",
        "action": "reconnect",
        **{k: task[k] for k in ("task_id", "lease_id", "runtime_id")},
        "nonce": "a" * 32,
        "sent_at": now,
        "meet_session_id": "ms_" + "b" * 32,
        "attempt": 0,
    }
    response = {
        "schema": "ananta.meet-reconnect-state.v1",
        "nonce": request["nonce"],
        "attempt": 1,
        "state": "waiting",
        "deadline_ms": (now + 30) * 1000,
        "ready_ms": (now + 4) * 1000,
        "meeting": None,
    }
    return task, request, response, now


def test_explicit_true_negotiation_preserves_legacy_closed_assignment():
    task, _, _, now = fixture()
    assert validate_assignment(task, now)["reconnect"] is True
    legacy = dict(task)
    del legacy["reconnect"]
    assert "reconnect" not in validate_assignment(legacy, now)
    for flag in (False, 1, "true", None):
        with pytest.raises(ValueError, match="negotiation_invalid"):
            validate_assignment(task | {"reconnect": flag}, now)


def test_reconnect_request_is_closed_and_other_actions_cannot_carry_attempts():
    _, request, _, now = fixture()
    for attempt in (0, 1, 2):
        assert validate_callback(request | {"attempt": attempt}, now)["attempt"] == attempt
    for value in (False, -1, 3, "1", 1.0, None):
        with pytest.raises(ValueError):
            validate_callback(request | {"attempt": value}, now)
    for patch in ({"force": True}, {"grant": "self-issued"}, {"action": "exchange"}, {"action": "chat"}):
        with pytest.raises(ValueError):
            validate_callback(request | patch, now)


def test_initial_response_then_bounded_pending_and_single_grant_handoff():
    task, request, value, now = fixture()
    assert validate_reconnect_response(value, request, task, now * 1000, 0) == value
    pending = request | {"attempt": 1}
    assert validate_reconnect_response(value, pending, task, now * 1000, 1) == value
    issued = value | {"state": "joining", "meeting": task["meeting"] | {"grant": "fresh-synthetic"}}
    accepted = validate_reconnect_response(issued, pending, task, (now + 4) * 1000, 1)
    issued["meeting"]["grant"] = "mutated"
    assert accepted["meeting"]["grant"] == "fresh-synthetic"
    assert (
        validate_reconnect_response(issued | {"meeting": None}, pending, task, (now + 5) * 1000, 1)["meeting"] is None
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"attempt": True},
        {"attempt": 0},
        {"attempt": 2},
        {"attempt": 3},
        {"state": "active"},
        {"state": []},
        {"nonce": "old"},
        {"schema": "ananta.meet-dialog-state.v1"},
        {"ready_ms": False},
        {"ready_ms": 0},
        {"deadline_ms": True},
        {"extra": True},
        {"meeting": {}},
    ],
)
def test_malformed_or_reordered_reconnect_response_never_becomes_a_grant(patch):
    task, request, value, now = fixture()
    with pytest.raises(ValueError):
        validate_reconnect_response(value | patch, request, task, now * 1000, 0)


def test_expired_quarantine_original_task_bound_and_unnegotiated_reply_fail_closed():
    task, request, value, now = fixture()
    for patch, at in [
        ({}, now + 30),
        ({"deadline_ms": (now + 31) * 1000}, now),
        ({"deadline_ms": task["deadline"] * 1000 + 1}, now),
        ({"ready_ms": value["deadline_ms"]}, now),
        ({"state": "joining", "meeting": task["meeting"]}, now + 3),
    ]:
        with pytest.raises(ValueError):
            validate_reconnect_response(value | patch, request, task, at * 1000, 0)
    with pytest.raises(ValueError):
        validate_reconnect_response(value, request, task | {"reconnect": False}, now * 1000, 0)


@pytest.mark.parametrize(
    "patch",
    [
        {"origin": "https://foreign.test"},
        {"room_id": "room-" + "f" * 18},
        {"origin": "http://meet.test"},
        {"grant": "SRC_self_assigned!"},
        {"force": True},
    ],
)
def test_fresh_grant_cannot_change_the_original_room_or_endpoint(patch):
    task, request, value, now = fixture()
    value = value | {"state": "joining", "meeting": task["meeting"] | patch}
    with pytest.raises(ValueError):
        validate_reconnect_response(value, request, task, (now + 4) * 1000, 0)


def test_second_recovery_requires_second_attempt_and_no_third_is_accepted():
    task, request, value, now = fixture()
    assert validate_reconnect_response(value | {"attempt": 2}, request, task, now * 1000, 1)["attempt"] == 2
    with pytest.raises(ValueError):
        validate_reconnect_response(value | {"attempt": 3}, request, task, now * 1000, 2)
    with pytest.raises(ValueError):
        validate_reconnect_response(value | {"attempt": 2}, request | {"attempt": 1}, task, now * 1000, 2)
