from __future__ import annotations

import pytest

from client_surfaces.operator_tui.tui_action_dispatcher import (
    ActionRequest,
    TuiActionDispatcher,
    TuiActionRegistry,
)


def _dispatcher(**state) -> TuiActionDispatcher:
    d = TuiActionDispatcher()
    d.set_tui_state(state)
    return d


def _req(action_id: str, **args) -> ActionRequest:
    return ActionRequest(action_id=action_id, args=args, source="test")


def test_help_tui_returns_ok():
    result = _dispatcher().dispatch(_req("help.tui"))
    assert result.is_ok()
    assert "TUI" in result.message


def test_view_list_returns_views():
    result = _dispatcher(available_views=["logo_animation", "renderer_diagnostics"]).dispatch(_req("view.list"))
    assert result.is_ok()
    assert "logo_animation" in result.message


def test_view_next_sets_cycle_flag():
    result = _dispatcher().dispatch(_req("view.next"))
    assert result.is_ok()
    assert result.changed_state_summary.get("visual_viewport_cycle_next") is True


def test_view_previous_sets_cycle_flag():
    result = _dispatcher().dispatch(_req("view.previous"))
    assert result.is_ok()
    assert result.changed_state_summary.get("visual_viewport_cycle_previous") is True


def test_view_select_sets_request():
    result = _dispatcher().dispatch(_req("view.select", view_id="renderer_diagnostics"))
    assert result.is_ok()
    assert result.changed_state_summary["visual_viewport_active_view_request"] == "renderer_diagnostics"


def test_view_select_missing_arg_returns_error():
    result = _dispatcher().dispatch(_req("view.select"))
    assert result.status == "error"


def test_overlay_on_sets_visible_true():
    result = _dispatcher(visual_view_switcher_overlay_visible=False).dispatch(_req("overlay.views.on"))
    assert result.is_ok()
    assert result.changed_state_summary["visual_view_switcher_overlay_visible"] is True


def test_overlay_off_sets_visible_false():
    result = _dispatcher(visual_view_switcher_overlay_visible=True).dispatch(_req("overlay.views.off"))
    assert result.is_ok()
    assert result.changed_state_summary["visual_view_switcher_overlay_visible"] is False


def test_overlay_toggle_flips_state():
    r1 = _dispatcher(visual_view_switcher_overlay_visible=False).dispatch(_req("overlay.views.toggle"))
    assert r1.changed_state_summary["visual_view_switcher_overlay_visible"] is True
    r2 = _dispatcher(visual_view_switcher_overlay_visible=True).dispatch(_req("overlay.views.toggle"))
    assert r2.changed_state_summary["visual_view_switcher_overlay_visible"] is False


def test_focus_chat_sets_target():
    result = _dispatcher().dispatch(_req("focus.chat"))
    assert result.is_ok()
    assert result.changed_state_summary["focus_target_request"] == "chat"


def test_artifact_open_sets_request():
    result = _dispatcher().dispatch(_req("artifact.open", ref="3"))
    assert result.is_ok()
    assert result.changed_state_summary["open_artifact_request"] == "3"


def test_artifact_open_missing_ref_returns_error():
    result = _dispatcher().dispatch(_req("artifact.open"))
    assert result.status == "error"


def test_snake_pause():
    result = _dispatcher().dispatch(_req("snake.pause"))
    assert result.is_ok()
    assert result.changed_state_summary["snake_paused_request"] is True


def test_snake_resume():
    result = _dispatcher().dispatch(_req("snake.resume"))
    assert result.is_ok()
    assert result.changed_state_summary["snake_paused_request"] is False


def test_snake_follow_on():
    result = _dispatcher().dispatch(_req("snake.follow.on"))
    assert result.is_ok()
    assert result.changed_state_summary["snake_mouse_follow_request"] is True


def test_unknown_action_returns_not_found():
    result = _dispatcher().dispatch(_req("does.not.exist"))
    assert result.status == "not_found"
    assert result.is_ok() is False


def test_dispatcher_exception_returns_structured_error():
    class BrokenRegistry(TuiActionRegistry):
        def get(self, action_id):
            action = super().get(action_id)
            if action and action_id == "view.next":
                raise RuntimeError("boom")
            return action

    d = TuiActionDispatcher(registry=BrokenRegistry())
    result = d.dispatch(_req("view.next"))
    assert result.status == "error"
    assert "boom" in result.message


def test_control_result_marker_present():
    result = _dispatcher().dispatch(_req("view.list"))
    assert "status" in result.control_result_marker
    assert result.control_result_marker["status"] == "ok"
    assert result.control_result_marker["action_id"] == "view.list"


def test_dispatcher_does_not_require_terminal():
    # All tests run without any terminal — this just confirms the suite can run
    d = _dispatcher()
    r = d.dispatch(_req("help.tui"))
    assert r.is_ok()
