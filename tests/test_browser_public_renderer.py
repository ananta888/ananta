"""Generation, frame and teardown fences without a browser or publication grant."""

from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ananta_contracts.browser_public_view import blocked_view
from ananta_contracts.browser_view_generation import BrowserViewGeneration
from tests.test_browser_public_view import ready
from worker.meet_media.browser_public_renderer import SanitizedDocumentView


def setup():
    browser, context, page, cdp = Mock(), Mock(), Mock(), Mock()
    browser.new_context.return_value = context
    context.new_page.return_value = page
    context.new_cdp_session.return_value = cdp
    context.pages = [page]
    page.url = "about:blank"
    page.evaluate.return_value = True
    clock = [100.0]
    generation = BrowserViewGeneration("workspace", "page", 1)
    view = SanitizedDocumentView(browser, generation, deadline=120.0, clock=lambda: clock[0])
    return SimpleNamespace(**locals())


def frame(data="synthetic-jpeg", **changes):
    return {"sessionId": 1, "data": data, "metadata": {"deviceWidth": 640, "deviceHeight": 360}} | changes


@pytest.mark.parametrize(
    "patch",
    [
        {"workspace_id": ""},
        {"page_id": "https://foreign"},
        {"navigation_revision": True},
        {"navigation_revision": 1.0},
        {"navigation_revision": 0},
        {"navigation_revision": 1024},
    ],
)
def test_generation_is_closed_and_not_coerced(patch):
    with pytest.raises(ValueError, match="generation_invalid"):
        BrowserViewGeneration(**({"workspace_id": "workspace", "page_id": "page", "navigation_revision": 1} | patch))


def test_generation_cannot_mutate_and_deadline_is_explicit_bounded_and_finite():
    generation = BrowserViewGeneration("workspace", "page", 1)
    with pytest.raises(FrozenInstanceError):
        generation.page_id = "foreign"
    for deadline in (None, True, float("nan"), float("inf"), 100, 131):
        browser = Mock()
        with pytest.raises(ValueError, match="deadline_invalid"):
            SanitizedDocumentView(browser, generation, deadline=deadline, clock=lambda: 100)
        browser.new_context.assert_not_called()


def test_frames_start_after_valid_render_and_latest_single_frame_has_one_second_age_limit():
    f = setup()
    f.view._receive(frame())
    assert f.view.pending is None
    f.view.render(ready(), f.generation)
    for value in ("first", "second", "latest"):
        f.view._receive(frame(value))
    assert f.view.take(f.generation) == "latest"
    assert f.view.take(f.generation) is None
    f.view._receive(frame())
    f.clock[0] += 1.01
    assert f.view.take(f.generation) is None
    f.view.close()
    f.context.close.assert_called_once()


@pytest.mark.parametrize(
    "change", ["navigation", "workspace", "page", "expired", "blocked", "malformed", "render_error"]
)
def test_failed_render_clears_old_frame_and_cannot_reopen_same_renderer(change):
    f = setup()
    f.view.render(ready(), f.generation)
    f.view._receive(frame())
    generation, value = f.generation, ready()
    if change == "navigation":
        generation = replace(generation, navigation_revision=2)
    elif change in {"workspace", "page"}:
        generation = replace(generation, **{change + "_id": "foreign"})
    elif change == "expired":
        f.clock[0] = 120
    elif change == "blocked":
        value = blocked_view("sensitive_content")
    elif change == "malformed":
        value["html"] = "private"
    else:
        f.page.evaluate.side_effect = RuntimeError("private renderer message")
    with pytest.raises(ValueError):
        f.view.render(value, generation)
    assert f.view.closed and f.view.pending is None
    with pytest.raises(ValueError, match="generation_inactive"):
        f.view.take(f.generation)
    with pytest.raises(ValueError, match="generation_inactive"):
        f.view.render(ready(), f.generation)
    f.view._receive(frame())
    assert f.view.pending is None
    f.context.close.assert_called_once()


@pytest.mark.parametrize("change", ["url", "page", "shape", "check_error", "expiry"])
def test_take_rechecks_exact_owned_page_dimensions_and_deadline_before_releasing_frame(change):
    f = setup()
    f.view.render(ready(), f.generation)
    f.view._receive(frame())
    if change == "url":
        f.page.url = "https://foreign"
    elif change == "page":
        f.context.pages.append(Mock())
    elif change == "shape":
        f.page.evaluate.return_value = False
    elif change == "check_error":
        f.page.evaluate.side_effect = RuntimeError()
    else:
        f.clock[0] = 120
    with pytest.raises(ValueError, match="generation_inactive"):
        f.view.take(f.generation)
    assert f.view.closed and f.view.pending is None


@pytest.mark.parametrize(
    "event",
    [
        None,
        frame(metadata=None),
        frame(None),
        frame(""),
        frame("x" * 350001),
        frame(metadata={}),
        frame(metadata={"deviceWidth": 1280, "deviceHeight": 360}),
    ],
)
def test_invalid_browser_frame_closes_generation_without_releasing_partial_bytes(event):
    f = setup()
    f.view.render(ready(), f.generation)
    f.view._receive(event)
    assert f.view.closed and f.view.pending is None


def test_teardown_is_idempotent_and_browser_failure_cannot_prevent_context_close():
    f = setup()
    f.cdp.send.side_effect = RuntimeError("private CDP error")
    f.view.close()
    f.view.close()
    f.context.close.assert_called_once()


@pytest.mark.parametrize("clock", [120.0, float("nan")])
def test_frame_callback_itself_closes_expired_or_invalid_clock_without_waiting_for_take(clock):
    f = setup()
    f.clock[0] = clock
    f.view._receive(frame())
    assert f.view.closed and f.view.pending is None
    f.context.close.assert_called_once()
