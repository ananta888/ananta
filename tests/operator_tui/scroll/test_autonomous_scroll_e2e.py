from __future__ import annotations

import pytest

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


# Each case runs headless without a terminal (formerly also covered by
# test_scroll_commands_require_no_terminal for pageup/pagedown/top/bottom).
@pytest.mark.parametrize(
    "cmd,expected_request",
    [
        pytest.param("/scroll pagedown", "page_down", id="page_down"),
        pytest.param("/scroll pageup", "page_up", id="page_up"),
        pytest.param("/scroll top", "home", id="top"),
        pytest.param("/scroll bottom", "end", id="bottom"),
        pytest.param("/scroll up", "line_up", id="line_up"),
        pytest.param("/scroll down", "line_down", id="line_down"),
    ],
)
def test_scroll_command_dispatches_request(cmd, expected_request):
    r = _run(cmd)
    assert r["ok"], f"{cmd} should pass but got: {r}"
    assert r["changed"]["scroll_command_request"] == expected_request
    assert r["marker"]["status"] == "ok"


def test_focus_logs_succeeds():
    r = _run("/focus logs")
    assert r["ok"] and r["changed"]["focus_target_request"] == "logs"


def test_focus_nav_succeeds():
    r = _run("/focus nav")
    assert r["ok"] and r["changed"]["focus_target_request"] == "nav"


@pytest.mark.parametrize(
    "cmd",
    [
        pytest.param("/scroll sideways", id="invalid_scroll_direction"),
        pytest.param("/scroll diagonal", id="nonexistent_scroll_target"),
    ],
)
def test_unknown_scroll_direction_denied(cmd):
    r = _run(cmd)
    assert not r["ok"]
