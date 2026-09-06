"""Headless operation timeouts use virtual time, never human approval."""

from unittest.mock import Mock

import pytest

from worker.meet_media.publication_session import PublicationSession

URL = "https://meet.example/machine"


class Page:
    def __init__(self):
        self.url = URL
        self.now = 0.0
        self.state = "done"
        self.is_ready = True
        self.calls = []

    def evaluate(self, expression, arg=None):
        if arg is not None:
            self.calls.append(arg)
            return
        if "Boolean" in expression:
            return self.is_ready
        return self.state

    def wait_for_timeout(self, timeout):
        assert 0 < timeout <= 250
        self.now += timeout / 1000


def session(page, lease=None, deadline=120):
    return PublicationSession(
        page,
        lease if lease is not None else Mock(),
        url=URL,
        deadline=deadline,
        clock=lambda: page.now,
        wall=lambda: 0,
    )


def test_join_publish_leave_are_single_attempt_lease_checked_operations():
    page, lease = Page(), Mock()
    current = session(page, lease)
    current.ready()
    for operation in ["join", "publish", "leave"]:
        current.call(operation, [])
    assert page.calls == [["join", [], URL], ["publish", [], URL], ["leave", [], URL]]
    assert lease.require.call_count == 11


@pytest.mark.parametrize("operation,budget", [("join", 20), ("publish", 85), ("leave", 3)])
def test_unresolved_operation_stops_at_phase_budget(operation, budget):
    page = Page()
    page.state = "pending"
    with pytest.raises(ValueError, match="meet_publication_expired"):
        session(page).call(operation, [])
    assert page.now == budget
    assert page.calls == [[operation, [], URL]]


def test_original_deadline_caps_operation_and_readiness():
    for readiness in (False, True):
        page = Page()
        page.state, page.is_ready = "pending", False
        current = session(page, deadline=0.6)
        with pytest.raises(ValueError, match="meet_publication_expired"):
            current.ready() if readiness else current.call("join", [])
        assert page.now == pytest.approx(0.6)


def test_missing_api_does_not_wait_indefinitely():
    page = Page()
    page.is_ready = False
    with pytest.raises(ValueError, match="meet_publication_expired"):
        session(page).ready()
    assert page.now == 20


@pytest.mark.parametrize("operation", ["join", "publish", "leave"])
def test_lease_revocation_interrupts_pending_promise_without_retry(operation):
    page, lease = Page(), Mock()
    page.state = "pending"
    lease.require.side_effect = [None, None, None, ValueError("synthetic_revocation")]
    with pytest.raises(ValueError, match="synthetic_revocation"):
        session(page, lease).call(operation, [])
    assert page.now == 0.25
    assert page.calls == [[operation, [], URL]]


@pytest.mark.parametrize("state", ["failed", "unknown", None, {}, True])
def test_rejected_or_invalid_page_state_never_counts_as_success(state):
    page = Page()
    page.state = state
    with pytest.raises(ValueError, match="meet_publication_operation_failed"):
        session(page).call("publish", [])


def test_revocation_after_done_still_rejects_success():
    page, lease = Page(), Mock()
    lease.require.side_effect = [None, None, ValueError("synthetic_revocation")]
    with pytest.raises(ValueError, match="synthetic_revocation"):
        session(page, lease).call("publish", [])


def test_expiry_inside_slow_lease_call_stops_before_browser_operation():
    page, lease = Page(), Mock()
    lease.require.side_effect = lambda: setattr(page, "now", 21)
    with pytest.raises(ValueError, match="meet_publication_expired"):
        session(page, lease).call("join", [])
    assert page.calls == []


def test_redirect_during_lease_check_stops_before_grant_handoff():
    page, lease = Page(), Mock()
    lease.require.side_effect = lambda: setattr(page, "url", "https://other.example/machine")
    with pytest.raises(ValueError, match="meet_machine_navigation_denied"):
        session(page, lease).call("join", ["room", "private-grant"])
    assert page.calls == []


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), "120", True])
def test_invalid_deadline_never_authorizes_operation(deadline):
    with pytest.raises(ValueError, match="meet_publication_deadline_invalid"):
        session(Page(), deadline=deadline)


def test_unknown_operation_is_not_dispatched():
    page = Page()
    with pytest.raises(ValueError, match="meet_publication_operation_invalid"):
        session(page).call("renew", [])
    assert not page.calls
