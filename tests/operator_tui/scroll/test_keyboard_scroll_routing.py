from __future__ import annotations

from client_surfaces.operator_tui.focus.focus_manager import FocusManager
from client_surfaces.operator_tui.scroll.scroll_context import ScrollContext
from client_surfaces.operator_tui.scroll.scroll_manager import ScrollManager


def _setup(active_focus: str = "chat_panel") -> tuple[ScrollManager, FocusManager]:
    sm = ScrollManager()
    fm = FocusManager()
    sm.register(ScrollContext(id="chat_panel", label="Chat", content_height=200, viewport_height=20))
    sm.register(ScrollContext(id="artifact_panel", label="Artifacts", content_height=150, viewport_height=20))
    fm.register_scroll_context("chat_panel", "chat_panel")
    fm.register_scroll_context("artifact_panel", "artifact_panel")
    fm.set_active(active_focus)
    return sm, fm


def _scroll(sm: ScrollManager, fm: FocusManager, direction: str) -> bool:
    ctx_id = fm.active_scroll_context_id()
    if ctx_id is None:
        return False
    ctx = sm.get(ctx_id)
    if ctx is None:
        return False
    if direction == "page_up":
        return ctx.scroll_page_up()
    if direction == "page_down":
        return ctx.scroll_page_down()
    if direction == "line_up":
        return ctx.scroll_line_up()
    if direction == "line_down":
        return ctx.scroll_line_down()
    return False


def test_pagedown_scrolls_focused_context():
    sm, fm = _setup("chat_panel")
    moved = _scroll(sm, fm, "page_down")
    assert moved
    assert sm.get("chat_panel").offset > 0


def test_pageup_scrolls_focused_context():
    sm, fm = _setup("chat_panel")
    sm.get("chat_panel").scroll_end()
    moved = _scroll(sm, fm, "page_up")
    assert moved
    assert sm.get("chat_panel").offset < sm.get("chat_panel").max_scroll


def test_focus_change_routes_to_different_context():
    sm, fm = _setup("chat_panel")
    fm.set_active("artifact_panel")
    _scroll(sm, fm, "page_down")
    assert sm.get("artifact_panel").offset > 0
    assert sm.get("chat_panel").offset == 0


def test_no_active_scroll_context_produces_noop():
    sm, fm = _setup("main_content")
    fm.set_active("nonexistent_panel")
    ctx_id = fm.active_scroll_context_id()
    assert ctx_id is None


def test_ctrl_w_style_cycle_changes_focus():
    sm, fm = _setup("chat_panel")
    order = ["chat_panel", "artifact_panel"]
    new_focus = fm.cycle_next(order)
    assert new_focus == "artifact_panel"
    assert fm.active() == "artifact_panel"


def test_focus_left_right_behavior():
    sm, fm = _setup("chat_panel")
    order = ["chat_panel", "artifact_panel"]
    fm.set_focus_order(order)
    fm.cycle_next()
    assert fm.active() == "artifact_panel"
    fm.cycle_previous()
    assert fm.active() == "chat_panel"
