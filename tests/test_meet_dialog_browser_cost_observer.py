"""The cost observer preserves native calls, failures and bounded privacy."""

from unittest.mock import Mock

import pytest
from playwright.sync_api import CDPSession, Page

from tests.meet_dialog_browser_cost_observer import DialogBrowserCostObserver, operation_name


def test_inclusive_nested_ack_cost_is_separate_without_additional_calls(monkeypatch):
    now = [100.0]
    send = Mock(side_effect=lambda *args, **kwargs: now.__setitem__(0, now[0] + 0.3))
    monkeypatch.setattr(CDPSession, "send", send)

    def evaluate(*args, **kwargs):
        CDPSession.send(object(), "Page.screencastFrameAck", {"private": "pixels"})
        now[0] += 0.1
        return "private-result"

    native = Mock(side_effect=evaluate)
    monkeypatch.setattr(Page, "evaluate", native)
    observer = DialogBrowserCostObserver(monkeypatch, clock=lambda: now[0])
    assert Page.evaluate(object(), "window.anantaMachine.screen.status()", "private-arg") == "private-result"
    report = observer.report()
    assert send.call_count == native.call_count == 1
    assert report["inclusive_costs_not_additive"] is True
    assert report["operations"]["screen"]["max_ms"] == 400
    assert report["operations"]["source_ack"]["max_ms"] == 300
    assert "private" not in str(report)


def test_totals_and_tail_are_bounded_and_detached_from_report(monkeypatch):
    ticks = iter(i / 10 for i in range(60))
    native = Mock(return_value="actual")
    monkeypatch.setattr(Page, "wait_for_timeout", native)
    observer = DialogBrowserCostObserver(monkeypatch, clock=lambda: next(ticks))
    for _ in range(25):
        assert Page.wait_for_timeout(object(), 100) == "actual"
    report = observer.report()
    assert native.call_count == 25 and len(report["recent"]) == 20
    assert report["operations"]["idle"]["calls"] == 25
    assert sum(report["operations"]["idle"]["buckets"]) == 25
    report["operations"]["idle"]["buckets"][0] = 99
    report["recent"].clear()
    assert len(observer.report()["recent"]) == 20
    assert observer.report()["operations"]["idle"]["buckets"][0] == 0


@pytest.mark.parametrize("method,tag", [("Page.screencastFrameAck", "source_ack"), ("private-command", "other_cdp")])
def test_exception_identity_and_original_arguments_preserved(monkeypatch, method, tag):
    error = RuntimeError("private-error")
    native = Mock(side_effect=error)
    monkeypatch.setattr(CDPSession, "send", native)
    ticks = iter([1.0, 1.1])
    observer = DialogBrowserCostObserver(monkeypatch, clock=lambda: next(ticks))
    target = object()
    with pytest.raises(RuntimeError) as caught:
        CDPSession.send(target, method, params={"private": "value"})
    assert caught.value is error
    native.assert_called_once_with(target, method, params={"private": "value"})
    assert observer.report()["operations"][tag]["calls"] == 1
    assert "private" not in str(observer.report())


@pytest.mark.parametrize(
    "expression,tag",
    [
        ("({iceCounts:window.__testIce})", "test_diagnostics"),
        ("window.__testPcs.map(x => x)", "test_diagnostics"),
        ("Boolean(document.querySelector('input'))", "source_content"),
        ("document.getElementById('activity')", "source_activity"),
        ("window.anantaMachine.timing.snapshot()", "timing"),
        ("window.__anantaSpeechPlayback", "speech"),
        ("window.anantaMachine.screen.status()", "screen"),
        ("private expression", "other_evaluate"),
    ],
)
def test_only_closed_operation_tags_are_retained(expression, tag):
    assert operation_name(expression) == tag
