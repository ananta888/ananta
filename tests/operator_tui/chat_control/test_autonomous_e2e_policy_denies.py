from __future__ import annotations

from client_surfaces.operator_tui.chat_control_config import ChatControlConfig
from client_surfaces.operator_tui.chat_control_parser import ParsedCommand, parse_chat_command
from client_surfaces.operator_tui.chat_control_policy import evaluate


_NARROW_ALLOWLIST = ChatControlConfig(
    mode="autonomous_e2e",
    e2e_allowlist=("view.list",),
)
_FULL_AUTONOMOUS = ChatControlConfig(mode="autonomous_e2e")


def _deny(cmd: str, config: ChatControlConfig = _FULL_AUTONOMOUS) -> dict:
    d = evaluate(parse_chat_command(cmd), config=config)
    return {"verdict": d.verdict, "reason": d.reason, "mode": d.mode}


def test_unknown_command_denied_in_autonomous():
    r = _deny("/unknown xyz")
    assert r["verdict"] == "deny"
    assert r["mode"] == "autonomous_e2e"


def test_shell_like_command_denied():
    r = _deny("/rm -rf /")
    assert r["verdict"] == "deny"


def test_destructive_like_command_denied():
    r = _deny("/delete everything")
    assert r["verdict"] == "deny"


def test_out_of_allowlist_action_denied():
    r = _deny("/snake pause", _NARROW_ALLOWLIST)
    assert r["verdict"] == "deny"
    assert "allowlist" in r["reason"].lower()


def test_denial_reason_code_present():
    d = evaluate(parse_chat_command("/unknown action"), config=_FULL_AUTONOMOUS)
    assert d.verdict == "deny"
    assert d.reason


def test_allowed_action_not_denied():
    d = evaluate(parse_chat_command("/view list"), config=_FULL_AUTONOMOUS)
    assert d.verdict == "allow"


def test_narrowed_allowlist_blocks_view_next():
    config = ChatControlConfig(mode="autonomous_e2e", e2e_allowlist=("help.tui",))
    d = evaluate(parse_chat_command("/view next"), config=config)
    assert d.verdict == "deny"
