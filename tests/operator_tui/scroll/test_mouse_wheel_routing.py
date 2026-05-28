from __future__ import annotations

from client_surfaces.operator_tui.focus.focus_manager import FocusManager
from client_surfaces.operator_tui.input.mouse_router import MouseRouter, PanelRect


def _fm(active: str = "chat_panel") -> FocusManager:
    fm = FocusManager()
    fm.register_scroll_context("chat_panel", "chat_panel")
    fm.register_scroll_context("artifact_panel", "artifact_panel")
    fm.register_scroll_context("center_viewport", "center_viewport")
    fm.set_active(active)
    return fm


def _router() -> MouseRouter:
    mr = MouseRouter()
    mr.register_panel(PanelRect(x1=0, y1=10, x2=30, y2=40, focus_id="chat_panel", scroll_context_id="chat_panel"))
    mr.register_panel(PanelRect(x1=31, y1=10, x2=60, y2=40, focus_id="artifact_panel", scroll_context_id="artifact_panel"))
    mr.register_panel(PanelRect(x1=0, y1=0, x2=60, y2=9, focus_id="center_viewport", scroll_context_id="center_viewport"))
    return mr


def test_wheel_over_chat_panel_routes_to_chat():
    mr = _router()
    ctx_id = mr.route_wheel_event(x=10, y=20, delta=1, focus_manager=_fm("artifact_panel"))
    assert ctx_id == "chat_panel"


def test_wheel_over_artifact_panel_routes_to_artifact():
    mr = _router()
    ctx_id = mr.route_wheel_event(x=40, y=20, delta=1, focus_manager=_fm("chat_panel"))
    assert ctx_id == "artifact_panel"


def test_wheel_over_center_viewport():
    mr = _router()
    ctx_id = mr.route_wheel_event(x=10, y=5, delta=-1, focus_manager=_fm("chat_panel"))
    assert ctx_id == "center_viewport"


def test_wheel_outside_panels_falls_back_to_focused():
    mr = _router()
    fm = _fm("chat_panel")
    ctx_id = mr.route_wheel_event(x=999, y=999, delta=1, focus_manager=fm)
    assert ctx_id == "chat_panel"


def test_focus_only_mode_ignores_cursor():
    mr = _router()
    fm = _fm("artifact_panel")
    ctx_id = mr.route_wheel_event(x=10, y=20, delta=1, focus_manager=fm, focus_only=True)
    assert ctx_id == "artifact_panel"


def test_mouse_support_disabled_falls_back():
    mr = MouseRouter(mouse_support_enabled=False)
    mr.register_panel(PanelRect(x1=0, y1=0, x2=50, y2=50, focus_id="chat_panel", scroll_context_id="chat_panel"))
    fm = _fm("artifact_panel")
    ctx_id = mr.route_wheel_event(x=10, y=10, delta=1, focus_manager=fm)
    assert ctx_id == "artifact_panel"


def test_no_panels_returns_focused():
    mr = MouseRouter()
    fm = _fm("chat_panel")
    ctx_id = mr.route_wheel_event(x=10, y=10, delta=1, focus_manager=fm)
    assert ctx_id == "chat_panel"


def test_wheel_does_not_affect_snake_movement_context():
    mr = _router()
    fm = _fm()
    ctx_id = mr.route_wheel_event(x=10, y=20, delta=1, focus_manager=fm)
    assert ctx_id != "snake_movement"


def test_diagnostics():
    mr = _router()
    d = mr.diagnostics()
    assert d["panel_count"] == 3
    assert d["mouse_support_enabled"] is True


def test_clear_panels():
    mr = _router()
    mr.clear_panels()
    assert mr.panels() == []
