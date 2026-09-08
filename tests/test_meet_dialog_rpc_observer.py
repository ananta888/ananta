"""Read-only timing diagnostics must not add calls or retain private arguments."""

from unittest.mock import Mock

from playwright.sync_api import Page

from tests.meet_dialog_rpc_observer import DialogRpcObserver


def test_rpc_observer_retains_only_bounded_tags_and_timings(monkeypatch):
    result = {"private": "synthetic-result"}
    evaluate, screenshot = Mock(return_value=result), Mock(return_value=b"synthetic-private-pixels")
    monkeypatch.setattr(Page, "evaluate", evaluate)
    monkeypatch.setattr(Page, "screenshot", screenshot)
    ticks = iter(value / 20 for value in range(100))
    observer = DialogRpcObserver(monkeypatch, clock=lambda: next(ticks))
    for _ in range(22):
        assert Page.evaluate(object(), "synthetic-private-expression", {"private": "argument"}) is result
    assert Page.screenshot(object()) == b"synthetic-private-pixels"
    report = observer.report()
    assert len(report) == 20 and evaluate.call_count == 22 and screenshot.call_count == 1
    assert all(set(row) == {"operation", "started_at_ms", "elapsed_ms"} and row["elapsed_ms"] == 50 for row in report)
    assert report[-1]["operation"] == "screenshot"
    assert "private" not in str(report)
    report[-1]["operation"] = "modified"
    assert observer.report()[-1]["operation"] == "screenshot"


def test_rpc_categories_include_case_insensitive_playback_and_preserve_native_failure(monkeypatch):
    import pytest

    error = RuntimeError("private failure")
    native = Mock(side_effect=error)
    monkeypatch.setattr(Page, "evaluate", native)
    ticks = iter([10, 11.32])
    observer = DialogRpcObserver(monkeypatch, clock=lambda: next(ticks))
    with pytest.raises(RuntimeError) as caught:
        Page.evaluate(object(), "window.__anantaSpeechPlayback.status", "private input")
    assert caught.value is error and native.call_count == 1
    assert observer.report() == [{"operation": "speech", "started_at_ms": 10000.0, "elapsed_ms": 1320.0}]
