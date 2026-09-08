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
