"""Composition order only; separate phase and real-browser tests verify transport."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from worker.meet_media import dialog_runtime


def setup(monkeypatch, *, running):
    events = []
    page = Mock(url="https://synthetic.test/machine")
    lease = {"sessionId": "synthetic-session"}
    page.evaluate.return_value = {"joined": True, "lease": lease}
    browser = Mock()
    browser.new_context.return_value.new_page.return_value = page
    browser.close.side_effect = lambda: events.append("browser.close")
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kwargs: browser))
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: nullcontext(playwright))
    monkeypatch.setattr(dialog_runtime, "time", SimpleNamespace(monotonic=lambda: 0))
    instances = {}
    for name, symbol in [
        ("session", "DialogSessionOperations"),
        ("speech", "DialogSpeechOutput"),
        ("chat", "DialogChatPump"),
        ("screen", "DialogScreenPump"),
        ("avatar", "DialogAvatarPump"),
        ("exchange", "DialogControlExchange"),
    ]:
        instance = Mock()
        instances[name] = instance
        for method in ["ready", "join", "renew", "leave", "close", "invalidate", "refresh", "update", "tick"]:
            getattr(instance, method).side_effect = lambda *args, tag=f"{name}.{method}", **kwargs: events.append(tag)
        monkeypatch.setattr(dialog_runtime, symbol, Mock(return_value=instance))
    instances["chat"].needs_refresh = False
    assignment = {
        "capabilities": ["chat.read", "chat.send", "screen.publish"],
        "meeting": {"origin": "https://synthetic.test", "room_id": "synthetic-room", "grant": "synthetic-grant"},
    }
    hub = SimpleNamespace(deadline=100 if running else -1)
    return SimpleNamespace(**locals())


def test_normal_deadline_closes_all_sources_before_bounded_leave_then_closes_browser(monkeypatch):
    f = setup(monkeypatch, running=False)
    dialog_runtime.run(f.assignment, f.hub)
    assert f.events.index("session.ready") < f.events.index("session.join")
    for name in ["speech", "chat", "screen", "avatar"]:
        assert f.events.index(f"{name}.close") < f.events.index("session.leave")
    assert f.events.index("session.leave") < f.events.index("browser.close")
    f.instances["exchange"].poll.assert_not_called()
    methods = [c[0] for c in f.browser.new_context.return_value.mock_calls]
    assert methods.index("route") < methods.index("new_page")
    f.browser.new_context.return_value.route_web_socket.assert_not_called()
    f.page.wait_for_function.assert_not_called()


@pytest.mark.parametrize("failure", [False, True])
def test_renewal_invalidates_sources_first_and_requires_fresh_exchange_before_output(monkeypatch, failure):
    f = setup(monkeypatch, running=True)
    state = {
        "authorization": {"lease": f.lease, "roomId": "synthetic-room"},
        "controls": {"revision": 1},
        "renewal": "synthetic-renewal",
    }
    f.instances["exchange"].poll.side_effect = [state, ValueError("synthetic_stop")]
    if failure:
        f.instances["session"].renew.side_effect = ValueError("synthetic_stale")
    with pytest.raises(ValueError, match="synthetic_stale" if failure else "synthetic_stop"):
        dialog_runtime.run(f.assignment, f.hub)
    for name in ["screen", "avatar", "chat"]:
        if not failure:
            assert f.events.index(f"{name}.invalidate") < f.events.index("session.renew")
        f.instances[name].update.assert_not_called()
        f.instances[name].tick.assert_not_called()
    f.instances["session"].renew.assert_called_once_with("synthetic-renewal", f.instances["exchange"].require_fresh)
    assert f.instances["exchange"].refresh.call_count == int(not failure)
    f.instances["session"].leave.assert_not_called()
    assert f.events[-1] == "browser.close"


def test_failed_leave_still_closes_owned_browser_and_never_retries(monkeypatch):
    f = setup(monkeypatch, running=False)
    f.instances["session"].leave.side_effect = ValueError("synthetic_leave_timeout")
    with pytest.raises(ValueError, match="leave_timeout"):
        dialog_runtime.run(f.assignment, f.hub)
    f.instances["session"].leave.assert_called_once()
    f.browser.close.assert_called_once()
    assert f.events[-1] == "browser.close"


@pytest.mark.parametrize("failed_progress", [False, True])
def test_progress_reports_original_freshness_only_after_valid_binding_before_renewal(monkeypatch, failed_progress):
    f = setup(monkeypatch, running=True)
    f.instances["exchange"].fresh_until = 2.5
    state = {
        "authorization": {"lease": f.lease, "roomId": "synthetic-room"},
        "controls": {"revision": 1},
        "renewal": "synthetic-renewal",
    }
    f.instances["exchange"].poll.side_effect = [state, ValueError("synthetic_stop")]
    progress = Mock()
    if failed_progress:
        progress.report.side_effect = ValueError("synthetic_progress_failed")
    with pytest.raises(ValueError, match="synthetic_progress_failed" if failed_progress else "synthetic_stop"):
        dialog_runtime.run(f.assignment, f.hub, progress=progress)
    progress.report.assert_called_once_with(2.5, 100)
    assert f.instances["session"].renew.call_count == (0 if failed_progress else 1)
    assert f.events[-1] == "browser.close"


def test_invalid_membership_never_emits_resource_progress(monkeypatch):
    f = setup(monkeypatch, running=True)
    f.instances["exchange"].poll.return_value = {
        "authorization": {"lease": f.lease, "roomId": "foreign-room"},
        "controls": {"revision": 1},
        "renewal": None,
    }
    progress = Mock()
    with pytest.raises(ValueError):
        dialog_runtime.run(f.assignment, f.hub, progress=progress)
    progress.report.assert_not_called()


def test_confirmed_disconnect_closes_sources_before_hub_recovery_and_recreates_them(monkeypatch):
    f = setup(monkeypatch, running=True)
    f.assignment["reconnect"] = True
    f.instances["session"].closed = False
    state = {
        "authorization": {"lease": f.lease, "roomId": "synthetic-room"},
        "controls": {"revision": 1},
        "renewal": "synthetic-renewal",
    }
    values = iter([state])

    def poll(**kwargs):
        value = next(values, None)
        if value is not None:
            return value
        f.page.evaluate.return_value = {"joined": False, "lease": None}
        raise ValueError("synthetic_transport_lost")

    f.instances["exchange"].poll.side_effect = poll

    def recover(assignment, hub, page, session, old_session, **kwargs):
        assert old_session == "synthetic-session"
        f.events.append("hub.reconnect")
        page.evaluate.return_value = {"joined": True, "lease": {"sessionId": "synthetic-new"}}
        hub.deadline = -1  # End this composition-only test after replacing pumps.

    reconnect = Mock(side_effect=recover)
    monkeypatch.setattr(dialog_runtime, "reconnect_session", reconnect)
    dialog_runtime.run(f.assignment, f.hub)
    reconnect.assert_called_once()
    for name in ["speech", "chat", "screen", "avatar", "exchange"]:
        assert f.events.index(f"{name}.close") < f.events.index("session.leave") < f.events.index("hub.reconnect")
    assert dialog_runtime.DialogSessionOperations.call_count == 2
    assert dialog_runtime.DialogScreenPump.call_count == 2
    assert dialog_runtime.DialogControlExchange.call_args.args[1] == "synthetic-new"
    assert f.instances["session"].leave.call_count == 2 and f.events[-1] == "browser.close"


@pytest.mark.parametrize("condition", ["unconfirmed", "joined", "cleanup_failed", "leave_failed"])
def test_recovery_never_hands_off_when_membership_or_cleanup_is_unconfirmed(monkeypatch, condition):
    f = setup(monkeypatch, running=True)
    f.assignment["reconnect"] = True
    f.instances["session"].closed = False
    state = {
        "authorization": {"lease": f.lease, "roomId": "synthetic-room"},
        "controls": {"revision": 1},
        "renewal": "synthetic-renewal",
    }
    values = iter([] if condition == "unconfirmed" else [state])

    def poll(**kwargs):
        value = next(values, None)
        if value is not None:
            return value
        f.page.evaluate.return_value = {"joined": condition == "joined", "lease": None}
        raise ValueError("synthetic_loss_or_denial")

    f.instances["exchange"].poll.side_effect = poll
    if condition == "cleanup_failed":
        f.instances["screen"].close.side_effect = ValueError("synthetic_cleanup")
    if condition == "leave_failed":
        f.instances["session"].leave.side_effect = ValueError("synthetic_leave")
    reconnect = Mock()
    monkeypatch.setattr(dialog_runtime, "reconnect_session", reconnect)
    with pytest.raises(ValueError):
        dialog_runtime.run(f.assignment, f.hub)
    reconnect.assert_not_called()
    f.browser.close.assert_called_once()


def test_worker_executes_at_most_two_hub_recoveries_under_one_original_assignment(monkeypatch):
    f = setup(monkeypatch, running=True)
    f.assignment["reconnect"] = True
    joined = Mock(side_effect=dialog_runtime.DialogMembershipLost("synthetic-confirmed"))
    reconnect = Mock()
    monkeypatch.setattr(dialog_runtime, "_run_joined", joined)
    monkeypatch.setattr(dialog_runtime, "reconnect_session", reconnect)
    with pytest.raises(dialog_runtime.DialogMembershipLost):
        dialog_runtime.run(f.assignment, f.hub)
    assert joined.call_count == 3 and reconnect.call_count == 2
    assert dialog_runtime.DialogSessionOperations.call_count == 3
    assert all(call.args[:2] == (f.assignment, f.hub) for call in reconnect.call_args_list)
    assert f.hub.deadline == 100
    f.browser.close.assert_called_once()
