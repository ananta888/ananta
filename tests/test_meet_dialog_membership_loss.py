"""Synthetic local membership; no network, user approval or inferred rejoin."""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from worker.meet_media.dialog_membership_loss import DialogMembershipLost, MembershipCheckpoint


def setup():
    page = Mock(url="https://synthetic.test/machine")
    page.evaluate.return_value = {"joined": False, "lease": None}
    session = SimpleNamespace(closed=False)
    now = [100.0]
    guard = MembershipCheckpoint(
        enabled=True,
        page=page,
        session=session,
        url=page.url,
        deadline=200,
        clock=lambda: now[0],
    )
    guard.confirm({"lease": {"sessionId": "synthetic-confirmed"}})
    return SimpleNamespace(**locals())


def test_only_confirmed_lost_membership_becomes_a_content_free_recovery_signal():
    f = setup()
    with pytest.raises(DialogMembershipLost) as caught, f.guard.guard():
        raise ValueError("PRIVATE_TRANSPORT_ERROR")
    assert caught.value.session_id == "synthetic-confirmed"
    assert str(caught.value) == "meet_dialog_confirmed_membership_lost"


@pytest.mark.parametrize(
    "condition",
    [
        "legacy",
        "unconfirmed",
        "session_closed",
        "expired",
        "navigated",
        "joined",
        "ambiguous",
        "renderer_failed",
        "expired_during_probe",
        "navigated_during_probe",
    ],
)
def test_setup_policy_renderer_navigation_or_unknown_membership_never_becomes_recovery(condition):
    f = setup()
    if condition == "legacy":
        f.guard.enabled = False
    elif condition == "unconfirmed":
        f.guard.confirmed = None
    elif condition == "session_closed":
        f.session.closed = True
    elif condition == "expired":
        f.now[0] = 200
    elif condition == "navigated":
        f.page.url += "/foreign"
    elif condition == "joined":
        f.page.evaluate.return_value = {"joined": True}
    elif condition == "ambiguous":
        f.page.evaluate.return_value = {"joined": 0}
    elif condition == "renderer_failed":
        f.page.evaluate.side_effect = ValueError("renderer_failed")
    else:

        def probe(*args):
            if condition == "expired_during_probe":
                f.now[0] = 200
            else:
                f.page.url += "/foreign"
            return {"joined": False}

        f.page.evaluate.side_effect = probe
    original = ValueError("synthetic_failure")
    with pytest.raises(ValueError) as caught, f.guard.guard():
        raise original
    assert caught.value is original


def test_failed_source_cleanup_overrides_recovery_signal():
    f = setup()
    cleanup_failure = ValueError("synthetic_cleanup_failed")
    with pytest.raises(ValueError) as caught, ExitStack() as cleanup, f.guard.guard():
        cleanup.callback(Mock(side_effect=cleanup_failure))
        raise ValueError("synthetic_lost")
    assert caught.value is cleanup_failure


def test_successful_iteration_does_not_probe_or_trigger_recovery():
    f = setup()
    with f.guard.guard():
        pass
    f.page.evaluate.assert_not_called()
