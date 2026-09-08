"""Current Hub source projection fences execution, independently of presentation."""

from copy import deepcopy

import pytest

from tests.test_meet_browser_workspace_contract import parent, projection
from worker.meet_media.browser_execution_lease import (
    BrowserExecutionLease,
    browser_execution_key,
    current_browser_source,
)


def binding_fixture():
    source, assignment = projection(), parent()
    assignment["meeting"] = {"origin": "https://meet.example.test", "room_id": "room-" + "a" * 18}
    receipt = {
        "lease": {"sessionId": source["binding"]["meet_session_id"], "generation": 1, "expiresAt": 35000},
        "peerId": source["binding"]["own_peer_id"],
        "roomId": assignment["meeting"]["room_id"],
        "membershipEpoch": 2,
    }
    return source, assignment, receipt, {"enabled": True, "revision": 1, "since": 10000}


def test_only_current_exact_assignment_and_meet_scope_are_accepted():
    source, assignment, receipt, control = binding_fixture()
    assert current_browser_source(source, assignment, receipt, control, now_ms=10000) == source


@pytest.mark.parametrize(
    "case", ["room", "peer", "meet_session", "generation", "membership", "screen", "deadline", "expired", "parent"]
)
def test_borrowed_stale_or_extended_binding_is_denied(case):
    source, assignment, receipt, control = binding_fixture()
    now = 10000
    if case == "room":
        receipt["roomId"] = "other"
    elif case == "peer":
        receipt["peerId"] = "other"
    elif case == "meet_session":
        receipt["lease"]["sessionId"] = "other"
    elif case == "generation":
        receipt["lease"]["generation"] = 2
    elif case == "membership":
        receipt["membershipEpoch"] = 3
    elif case == "screen":
        control["revision"] = 2
    elif case == "deadline":
        receipt["lease"]["expiresAt"] = 36000
    elif case == "expired":
        now = 35000
    else:
        assignment["task_id"] = "other"
    with pytest.raises(ValueError):
        current_browser_source(source, assignment, receipt, control, now_ms=now)


def test_execution_clock_is_bounded_and_cannot_be_refreshed_after_staleness():
    source = projection()
    clock = [0.0]
    lease = BrowserExecutionLease(source, clock=lambda: clock[0], wall_clock=lambda: 10)
    assert lease.deadline == 25
    lease.require()
    clock[0] = 2
    lease.refresh(source)
    clock[0] = 4.5
    with pytest.raises(ValueError, match="expired"):
        lease.require()
    with pytest.raises(ValueError, match="expired"):
        lease.refresh(source)


def test_presentation_pause_does_not_extend_or_replace_the_navigation_task():
    source = projection()
    lease = BrowserExecutionLease(source, clock=lambda: 0, wall_clock=lambda: 10)
    paused = deepcopy(source)
    paused["binding"]["screen_revision"] = 2
    paused["mode"] = "off"
    assert browser_execution_key(paused) == browser_execution_key(source)
    lease.refresh(paused)
    assert lease.deadline == 25
    lease.close()
    with pytest.raises(ValueError):
        lease.require()


@pytest.mark.parametrize("field", ["meet_session_id", "generation", "membership_epoch", "deadline_ms"])
def test_membership_or_deadline_change_cannot_refresh_old_execution(field):
    source = projection()
    lease = BrowserExecutionLease(source, clock=lambda: 0, wall_clock=lambda: 10)
    changed = deepcopy(source)
    changed["binding"][field] = "other" if field == "meet_session_id" else changed["binding"][field] + 1
    with pytest.raises(ValueError, match="changed"):
        lease.refresh(changed)
    assert lease.closed
