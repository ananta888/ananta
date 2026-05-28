from __future__ import annotations

from unittest.mock import patch

from client_surfaces.operator_tui.chat_control_audit import AuditLog
from client_surfaces.operator_tui.chat_control_config import ChatControlConfig
from client_surfaces.operator_tui.chat_control_parser import parse_chat_command
from client_surfaces.operator_tui.chat_control_policy import evaluate
from client_surfaces.operator_tui.tui_action_dispatcher import ActionRequest, TuiActionDispatcher


def _run_autonomous(cmd: str, tui_state: dict | None = None) -> dict:
    cfg = ChatControlConfig(mode="autonomous_e2e")
    parsed = parse_chat_command(cmd)
    decision = evaluate(parsed, config=cfg)
    if not decision.allowed():
        return {"ok": False, "reason": decision.reason, "verdict": decision.verdict}
    dispatcher = TuiActionDispatcher()
    dispatcher.set_tui_state(tui_state or {})
    result = dispatcher.dispatch(ActionRequest(action_id=decision.action_id, args=decision.normalized_args, source="test"))
    return {
        "ok": result.is_ok(),
        "action_id": decision.action_id,
        "message": result.message,
        "changed": result.changed_state_summary,
        "marker": result.control_result_marker,
        "auto_confirmed": decision.auto_confirmed,
    }


def test_help_tui_succeeds():
    r = _run_autonomous("/help tui")
    assert r["ok"] is True
    assert r["marker"]["status"] == "ok"


def test_view_list_reports_views():
    r = _run_autonomous("/view list", {"available_views": ["logo_animation", "renderer_diagnostics"]})
    assert r["ok"] is True
    assert "logo_animation" in r["message"]


def test_view_select_changes_view():
    r = _run_autonomous("/view diagnostics")
    assert r["ok"] is True
    assert r["changed"].get("visual_viewport_active_view_request") == "renderer_diagnostics"


def test_overlay_views_on():
    r = _run_autonomous("/overlay views on", {"visual_view_switcher_overlay_visible": False})
    assert r["ok"] is True
    assert r["changed"]["visual_view_switcher_overlay_visible"] is True
    assert r["marker"]["status"] == "ok"


def test_view_next_cycles():
    r = _run_autonomous("/view next")
    assert r["ok"] is True
    assert r["changed"].get("visual_viewport_cycle_next") is True


def test_auto_confirmed_flag_set():
    r = _run_autonomous("/view list")
    assert r["auto_confirmed"] is True


def test_does_not_require_terminal():
    r = _run_autonomous("/snake pause")
    assert r["ok"] is True
    assert "paused" in r["marker"]


def test_does_not_require_mermaid_cli():
    r = _run_autonomous("/view markdown")
    assert r["ok"] is True


def test_focus_chat():
    r = _run_autonomous("/focus chat")
    assert r["ok"] is True
    assert r["changed"]["focus_target_request"] == "chat"
