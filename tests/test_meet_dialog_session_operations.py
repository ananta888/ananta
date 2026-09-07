"""Bounded disposable-session operations with virtual clocks and exact browser JS."""

import json
import subprocess
from unittest.mock import Mock

import pytest

from worker.meet_media.browser_session_phase import START, STATE
from worker.meet_media.dialog_session_operations import DialogSessionOperations

URL = "https://synthetic.test/machine"


class Page:
    def __init__(self):
        self.url, self.now = URL, 100.0
        self.state, self.ready = "done", True
        self.calls = []
        self.after_read = lambda: None

    def evaluate(self, expression, arg=None):
        self.calls.append((expression, arg))
        if expression == START:
            return None  # Never return the browser Promise.
        self.after_read()
        return self.ready if "Boolean" in expression else self.state

    def wait_for_timeout(self, milliseconds):
        assert 0 < milliseconds <= 100
        self.now = round(self.now + milliseconds / 1000, 9)


def setup(deadline=7300):
    page = Page()
    session = DialogSessionOperations(page, url=URL, deadline=deadline, clock=lambda: page.now)
    return page, session


def dispatch(session, operation, checkpoint=lambda: None):
    if operation == "join":
        session.join("synthetic-room", "synthetic-grant")
    elif operation == "renew":
        session.renew("synthetic-renewal", checkpoint)
    else:
        session.leave()


def starts(page):
    return [arg for expression, arg in page.calls if expression == START]


def test_dialog_lifetime_is_not_capped_to_standalone_publishers_120_seconds():
    page, session = setup()
    session.ready()
    session.join("synthetic-room", "synthetic-grant")
    page.now += 600
    checkpoint = Mock()
    session.renew("synthetic-renewal", checkpoint)
    session.leave()
    assert starts(page) == [
        ["join", ["synthetic-room", "synthetic-grant"], URL],
        ["renew", ["synthetic-renewal"], URL],
        ["leave", [], URL],
    ]
    assert checkpoint.call_count == 3 and session.closed
    with pytest.raises(ValueError):
        session.join("synthetic-room", "another-grant")
    assert len(starts(page)) == 3


@pytest.mark.parametrize("operation,budget", [("join", 20), ("renew", 2.5), ("leave", 3)])
def test_never_settling_operation_times_out_once_and_late_done_cannot_retry(operation, budget):
    page, session = setup()
    session.joined = operation != "join"
    page.state = "pending"
    with pytest.raises(ValueError, match="session_expired"):
        dispatch(session, operation)
    assert page.now == pytest.approx(100 + budget) and len(starts(page)) == 1 and session.closed
    page.state = "done"
    with pytest.raises(ValueError):
        dispatch(session, operation)
    assert len(starts(page)) == 1


@pytest.mark.parametrize("operation", ["ready", "join", "renew"])
def test_original_assignment_deadline_caps_setup_and_renewal(operation):
    page, session = setup(deadline=100.35)
    page.ready, page.state = False, "pending"
    session.joined = operation == "renew"
    with pytest.raises(ValueError, match="session_expired"):
        session.ready() if operation == "ready" else dispatch(session, operation)
    assert page.now == 100.35 and session.closed


def test_readiness_has_its_own_20_second_bound():
    page, session = setup()
    page.ready = False
    with pytest.raises(ValueError, match="session_expired"):
        session.ready()
    assert page.now == 120 and not starts(page)


def test_leave_after_assignment_expiry_has_only_its_own_cleanup_budget():
    page, session = setup(deadline=101)
    session.join("synthetic-room", "synthetic-grant")
    page.now, page.state = 102, "pending"
    with pytest.raises(ValueError, match="session_expired"):
        session.leave()
    assert page.now == 105 and [row[0] for row in starts(page)] == ["join", "leave"]


def test_remaining_hub_freshness_caps_renewal_and_is_never_refreshed_by_polling():
    page, session = setup()
    session.joined, page.state = True, "pending"

    def current():
        if page.now >= 100.4:
            raise ValueError("synthetic_control_stale")

    with pytest.raises(ValueError, match="control_stale"):
        session.renew("synthetic-renewal", current)
    assert page.now == 100.4 and session.closed and len(starts(page)) == 1


@pytest.mark.parametrize("mutation", ["navigation", "expired", "revoked"])
def test_renewal_checkpoint_cannot_change_page_time_or_authority_before_grant_handoff(mutation):
    page, session = setup()
    session.joined = True

    def current():
        if mutation == "navigation":
            page.url = "https://foreign.test/machine"
        elif mutation == "expired":
            page.now += 3
        else:
            raise ValueError("synthetic_revoked")

    with pytest.raises(ValueError):
        session.renew("synthetic-private-grant", current)
    assert session.closed and not page.calls


@pytest.mark.parametrize("mutation", ["navigation", "expired", "revoked"])
def test_done_receipt_does_not_override_post_rpc_authority_check(mutation):
    page, session = setup()
    session.joined = True
    checkpoint = Mock()

    def after_read():
        if mutation == "navigation":
            page.url += "/elsewhere"
        elif mutation == "expired":
            page.now += 3
        else:
            checkpoint.side_effect = ValueError("synthetic_revoked")

    page.after_read = after_read
    with pytest.raises(ValueError):
        session.renew("synthetic-renewal", checkpoint)
    assert session.closed and len(starts(page)) == 1


@pytest.mark.parametrize("state", ["failed", "unknown", None, {}, True])
def test_invalid_settlement_fails_closed(state):
    page, session = setup()
    page.state = state
    with pytest.raises(ValueError, match="operation_failed"):
        dispatch(session, "join")
    assert session.closed and len(starts(page)) == 1


def test_renewal_requires_both_existing_session_and_explicit_checkpoint():
    page, session = setup()
    with pytest.raises(ValueError, match="transition_invalid"):
        session.renew("synthetic", lambda: None)
    assert not starts(page)
    page, session = setup()
    session.joined = True
    with pytest.raises(ValueError, match="checkpoint_required"):
        session.renew("synthetic", None)
    assert session.closed and not starts(page)


@pytest.mark.parametrize("deadline", [None, True, "7300", float("nan"), float("inf")])
def test_invalid_deadline_cannot_start_browser_io(deadline):
    with pytest.raises(ValueError, match="deadline_invalid"):
        setup(deadline)


def test_shared_browser_js_fences_late_settlement_and_redacts_rejections():
    script = """
      const assert = require('node:assert/strict');
      const [start, state] = JSON.parse(process.argv[1]).map(s => eval('(' + s + ')'));
      let settle, calls = 0;
      global.window = {location:{href:'synthetic'}, anantaMachine:{
        renew() {calls++; return new Promise(resolve => {settle=resolve});},
        leave() {calls++; throw new Error('PRIVATE_REJECTION_CONTENT');}
      }};
      (async () => {
        assert.equal(start(['renew', ['private-grant'], 'synthetic']), undefined);
        await Promise.resolve(); const first = settle;
        start(['renew', ['next-private-grant'], 'synthetic']);
        await Promise.resolve(); const second = settle;
        first(); await new Promise(resolve => setImmediate(resolve));
        assert.equal(state(), 'pending');
        second(); await new Promise(resolve => setImmediate(resolve));
        assert.equal(state(), 'done');
        start(['leave', [], 'synthetic']);
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(state(), 'failed');
        assert.deepEqual(window.__anantaPublicationPhase, {operation:'leave', status:'failed'});
        window.location.href = 'foreign';
        assert.throws(() => start(['renew', [], 'synthetic']), /navigation_denied/);
        assert.equal(calls, 3);
      })().catch(e => {console.error(e);process.exitCode=1});
    """
    result = subprocess.run(["node", "-e", script, json.dumps([START, STATE])], capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr.decode()
