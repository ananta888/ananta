"""Workspace lifecycle seams; no Hub policy is delegated to a page or renderer."""

from unittest.mock import Mock

import pytest

from ananta_contracts.browser_view_generation import BrowserViewGeneration
from worker.meet_media.browser_public_workspace import PublicDocumentWorkspace


def workspace():
    browser, current, views, snapshots = Mock(), Mock(), Mock(), Mock()
    page = browser.new_context.return_value.new_page.return_value
    browser.new_context.return_value.pages = [page]
    page.url, page.is_closed.return_value, page.evaluate.return_value = "about:blank", False, True
    page.viewport_size = {"width": 640, "height": 360}
    snapshots.return_value.read.return_value = {
        "schema": "ananta.browser-public-view.v1",
        "state": "ready",
        "reason": None,
        "blocks": [{"kind": "text", "text": "Public"}],
    }
    generation = BrowserViewGeneration("hub-workspace", "hub-page", 1)
    value = PublicDocumentWorkspace(
        browser,
        generation,
        deadline=10,
        require_current=current,
        view_factory=views,
        snapshot_factory=snapshots,
        clock=lambda: 0,
    )
    return value, generation, browser, current, views, snapshots


def test_page_is_ephemeral_script_disabled_and_only_renderer_can_return_frames():
    value, generation, browser, current, views, _ = workspace()
    assert value.take(generation) is None
    browser.new_context.assert_not_called()
    value.load("<p>Public</p>", generation)
    options = browser.new_context.call_args.kwargs
    assert options["java_script_enabled"] is False and options["permissions"] == []
    assert options["accept_downloads"] is False and options["service_workers"] == "block"
    assert value.take(generation) is views.return_value.take.return_value
    views.return_value.render.assert_called_once()
    assert current.call_count >= 5
    value.close()
    value.close()
    views.return_value.close.assert_called_once()
    browser.new_context.return_value.close.assert_called_once()


def test_foreground_identity_checks_never_wait_for_a_crashed_execution_context():
    value, generation, _, _, views, _ = workspace()
    value.load("<p>Public</p>", generation)
    value.page.evaluate.side_effect = AssertionError("unbounded execution-context read forbidden")
    assert value.take(generation) is views.return_value.take.return_value
    value.page.evaluate.assert_not_called()


@pytest.mark.parametrize(
    "mode",
    [
        "generation",
        "revision",
        "revoked",
        "page_closed",
        "page_url",
        "resize",
        "extra_page",
        "expired",
        "clock_invalid",
    ],
)
def test_stale_scope_never_consumes_a_pending_view_frame(mode):
    value, generation, browser, current, views, _ = workspace()
    value.load("<p>Public</p>", generation)
    if mode == "generation":
        generation = BrowserViewGeneration("other", "hub-page", 1)
    elif mode == "revision":
        generation = BrowserViewGeneration("hub-workspace", "hub-page", 2)
    elif mode == "revoked":
        current.side_effect = ValueError("private reason")
    elif mode == "page_closed":
        value.page.is_closed.return_value = True
    elif mode == "page_url":
        value.page.url = "https://elsewhere.example"
    elif mode == "resize":
        value.page.viewport_size = {"width": 700, "height": 400}
    elif mode == "extra_page":
        value.context.pages.append(Mock())
    elif mode == "expired":
        value.clock = lambda: 11
    else:
        value.clock = lambda: float("nan")
    with pytest.raises(ValueError, match="^browser_workspace_inactive$"):
        value.take(generation)
    assert value.closed and value.last_snapshot is None
    views.return_value.take.assert_not_called()
    browser.new_context.return_value.close.assert_called_once()


def test_changed_snapshot_is_rendered_before_any_frame_and_denial_closes_both_contexts():
    value, generation, browser, _, views, snapshots = workspace()
    value.load("<p>Public</p>", generation)
    snapshots.return_value.read.return_value = {"state": "blocked"}
    views.return_value.render.side_effect = ValueError("browser_view_content_blocked")
    with pytest.raises(ValueError, match="^browser_workspace_view_denied$"):
        value.take(generation)
    views.return_value.take.assert_not_called()
    assert value.closed and value.last_snapshot is None
    views.return_value.close.assert_called_once()
    browser.new_context.return_value.close.assert_called_once()


@pytest.mark.parametrize("content", ["", None, "a" * 524289, "\ud800"])
def test_invalid_content_is_denied_before_context_creation(content):
    value, generation, browser, _, _, _ = workspace()
    with pytest.raises(ValueError, match="^browser_workspace_load_denied$"):
        value.load(content, generation)
    assert value.closed
    browser.new_context.assert_not_called()


def test_no_reload_with_old_generation_or_raw_frame_fallback():
    value, generation, browser, _, views, _ = workspace()
    value.load("<p>Public</p>", generation)
    with pytest.raises(ValueError, match="^browser_workspace_load_denied$"):
        value.load("<p>Other</p>", generation)
    assert value.closed
    browser.new_context.assert_called_once()
    views.return_value.take.assert_not_called()


def test_revocation_during_frame_take_discards_returned_frame():
    value, generation, _, current, views, _ = workspace()
    value.load("<p>Public</p>", generation)

    def take(_generation):
        current.side_effect = ValueError("revoked")
        return "must-not-be-returned"

    views.return_value.take.side_effect = take
    with pytest.raises(ValueError, match="^browser_workspace_view_denied$"):
        value.take(generation)
    assert value.closed


def test_crashed_renderer_does_not_keep_source_context_alive():
    value, generation, browser, _, views, _ = workspace()
    value.load("<p>Public</p>", generation)
    views.return_value.close.side_effect = RuntimeError("already gone")
    value.close()
    browser.new_context.return_value.close.assert_called_once()


def test_browser_event_invalidates_without_reentrant_rpc_then_foreground_closes():
    value, generation, browser, _, views, _ = workspace()
    value.load("<p>Public</p>", generation)
    views.return_value.pending = (0, "pending frame")
    callback = next(call.args[1] for call in value.context.on.call_args_list if call.args[0] == "page")
    callback(Mock())
    assert value.revoked and views.return_value.pending is None
    views.return_value.close.assert_not_called()
    browser.new_context.return_value.close.assert_not_called()
    with pytest.raises(ValueError, match="^browser_workspace_inactive$"):
        value.take(generation)
    views.return_value.take.assert_not_called()
    views.return_value.close.assert_called_once()
    browser.new_context.return_value.close.assert_called_once()
