from __future__ import annotations

from client_surfaces.operator_tui.focus.focus_manager import FocusManager


def test_default_active():
    fm = FocusManager()
    assert fm.active() == "main_content"


def test_set_active():
    fm = FocusManager()
    fm.set_active("chat_panel")
    assert fm.active() == "chat_panel"


def test_register_scroll_context():
    fm = FocusManager()
    fm.register_scroll_context("chat_panel", "chat_scroll")
    fm.set_active("chat_panel")
    assert fm.active_scroll_context_id() == "chat_scroll"


def test_active_scroll_context_none_when_not_registered():
    fm = FocusManager()
    fm.set_active("unknown_panel")
    assert fm.active_scroll_context_id() is None


def test_cycle_next():
    fm = FocusManager()
    order = ["a", "b", "c"]
    fm.set_focus_order(order)
    fm.set_active("a")
    result = fm.cycle_next(order)
    assert result == "b"


def test_cycle_next_wraps():
    fm = FocusManager()
    order = ["a", "b", "c"]
    fm.set_focus_order(order)
    fm.set_active("c")
    assert fm.cycle_next(order) == "a"


def test_cycle_previous():
    fm = FocusManager()
    order = ["a", "b", "c"]
    fm.set_focus_order(order)
    fm.set_active("b")
    assert fm.cycle_previous(order) == "a"


def test_cycle_previous_wraps():
    fm = FocusManager()
    order = ["a", "b", "c"]
    fm.set_focus_order(order)
    fm.set_active("a")
    assert fm.cycle_previous(order) == "c"


def test_deregister_scroll_context():
    fm = FocusManager()
    fm.register_scroll_context("chat_panel", "chat_scroll")
    fm.deregister_scroll_context("chat_panel")
    fm.set_active("chat_panel")
    assert fm.active_scroll_context_id() is None


def test_diagnostics_has_required_keys():
    fm = FocusManager()
    d = fm.diagnostics()
    assert "active_focus_id" in d
    assert "active_scroll_context_id" in d
    assert "registered" in d


def test_focus_changes_active_scroll_context():
    fm = FocusManager()
    fm.register_scroll_context("chat_panel", "chat_scroll")
    fm.register_scroll_context("artifact_panel", "artifact_scroll")
    fm.set_active("chat_panel")
    assert fm.active_scroll_context_id() == "chat_scroll"
    fm.set_active("artifact_panel")
    assert fm.active_scroll_context_id() == "artifact_scroll"
