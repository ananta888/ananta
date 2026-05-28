from __future__ import annotations

import pytest

from client_surfaces.operator_tui.chat_control_config import ChatControlConfig, _DEFAULT_E2E_ALLOWLIST
from client_surfaces.operator_tui.chat_control_parser import parse_chat_command
from client_surfaces.operator_tui.chat_control_policy import evaluate


_INTERACTIVE = ChatControlConfig(mode="interactive_safe")
_AUTONOMOUS = ChatControlConfig(mode="autonomous_e2e")


def _eval(cmd: str, config: ChatControlConfig = _INTERACTIVE):
    return evaluate(parse_chat_command(cmd), config=config)


def test_safe_command_allowed_interactive():
    decision = _eval("/view list")
    assert decision.allowed()
    assert decision.verdict == "allow"


def test_view_next_allowed_interactive():
    assert _eval("/view next").allowed()


def test_overlay_on_allowed():
    assert _eval("/overlay views on").allowed()


def test_snake_pause_allowed():
    assert _eval("/snake pause").allowed()


def test_unknown_command_denied():
    decision = _eval("/rm -rf /")
    assert not decision.allowed()
    assert decision.verdict == "deny"


def test_unknown_action_id_denied():
    from client_surfaces.operator_tui.chat_control_parser import ParsedCommand
    fake = ParsedCommand(raw_text="/foo bar", command="foo", subcommand="bar", args=(), error="", action_id="foo.bar")
    decision = evaluate(fake, config=_INTERACTIVE)
    assert not decision.allowed()


def test_parse_error_denied():
    from client_surfaces.operator_tui.chat_control_parser import ParsedCommand
    broken = ParsedCommand(raw_text="/bad", command="", subcommand="", args=(), error="unknown", action_id="")
    decision = evaluate(broken, config=_INTERACTIVE)
    assert not decision.allowed()


def test_autonomous_allowlisted_action_auto_confirmed():
    decision = _eval("/view list", _AUTONOMOUS)
    assert decision.allowed()
    assert decision.auto_confirmed is True
    assert decision.mode == "autonomous_e2e"


def test_autonomous_non_allowlisted_denied():
    config = ChatControlConfig(mode="autonomous_e2e", e2e_allowlist=("view.list",))
    decision = _eval("/snake pause", config)
    assert not decision.allowed()
    assert "allowlist" in decision.reason.lower()


def test_autonomous_unknown_command_denied():
    decision = _eval("/unknown cmd", _AUTONOMOUS)
    assert not decision.allowed()


def test_policy_includes_mode():
    decision = _eval("/view list", _INTERACTIVE)
    assert decision.mode == "interactive_safe"

    decision2 = _eval("/view list", _AUTONOMOUS)
    assert decision2.mode == "autonomous_e2e"


def test_all_default_e2e_allowlist_actions_pass_autonomous():
    from client_surfaces.operator_tui.tui_action_dispatcher import get_registry
    reg = get_registry()
    for action_id in _DEFAULT_E2E_ALLOWLIST:
        action = reg.get(action_id)
        if action is None:
            continue
        assert action.risk == "safe", f"{action_id} in allowlist but risk={action.risk}"
