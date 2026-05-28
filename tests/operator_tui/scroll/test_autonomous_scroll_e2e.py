from __future__ import annotations

from client_surfaces.operator_tui.chat_control_config import ChatControlConfig
from client_surfaces.operator_tui.chat_control_parser import parse_chat_command
from client_surfaces.operator_tui.chat_control_policy import evaluate
from client_surfaces.operator_tui.tui_action_dispatcher import ActionRequest, TuiActionDispatcher


def _run(cmd: str, mode: str = "autonomous_e2e", state: dict | None = None) -> dict:
    cfg = ChatControlConfig(mode=mode)
    parsed = parse_chat_command(cmd)
    decision = evaluate(parsed, config=cfg)
    if not decision.allowed():
        return {"ok": False, "verdict": decision.verdict, "reason": decision.reason}
    d = TuiActionDispatcher()
    d.set_tui_state(state or {})
    result = d.dispatch(ActionRequest(action_id=decision.action_id, args=decision.normalized_args, source="test"))
    return {"ok": result.is_ok(), "action_id": decision.action_id, "changed": result.changed_state_summary, "marker": result.control_result_marker}


def test_focus_chat_succeeds():
    r = _run("/focus chat")
    assert r["ok"] and r["changed"]["focus_target_request"] == "chat"


def test_focus_center_succeeds():
    r = _run("/focus center")
    assert r["ok"] and r["changed"]["focus_target_request"] == "center"


def test_scroll_page_down():
    r = _run("/scroll pagedown")
    assert r["ok"]
    assert r["changed"]["scroll_command_request"] == "page_down"
    assert r["marker"]["status"] == "ok"


def test_scroll_page_up():
    r = _run("/scroll pageup")
    assert r["ok"] and r["changed"]["scroll_command_request"] == "page_up"


def test_scroll_top():
    r = _run("/scroll top")
    assert r["ok"] and r["changed"]["scroll_command_request"] == "home"


def test_scroll_bottom():
    r = _run("/scroll bottom")
    assert r["ok"] and r["changed"]["scroll_command_request"] == "end"


def test_scroll_line_up():
    r = _run("/scroll up")
    assert r["ok"] and r["changed"]["scroll_command_request"] == "line_up"


def test_scroll_line_down():
    r = _run("/scroll down")
    assert r["ok"] and r["changed"]["scroll_command_request"] == "line_down"


def test_invalid_scroll_direction_denied():
    r = _run("/scroll sideways")
    assert not r["ok"]


def test_scroll_commands_require_no_terminal():
    for cmd in ["/scroll pageup", "/scroll pagedown", "/scroll top", "/scroll bottom"]:
        r = _run(cmd)
        assert r["ok"], f"{cmd} should pass but got: {r}"


def test_focus_logs_succeeds():
    r = _run("/focus logs")
    assert r["ok"] and r["changed"]["focus_target_request"] == "logs"


def test_focus_nav_succeeds():
    r = _run("/focus nav")
    assert r["ok"] and r["changed"]["focus_target_request"] == "nav"


def test_nonexistent_scroll_target_denied():
    r = _run("/scroll diagonal")
    assert not r["ok"]
