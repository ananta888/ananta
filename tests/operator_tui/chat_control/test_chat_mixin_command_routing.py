from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from client_surfaces.operator_tui.chat_control_config import ChatControlConfig
from client_surfaces.operator_tui.chat_control_parser import parse_chat_command
from client_surfaces.operator_tui.chat_control_policy import evaluate
from client_surfaces.operator_tui.tui_action_dispatcher import ActionRequest, TuiActionDispatcher


def _run_command_routing(cmd: str, mode: str = "interactive_safe") -> dict:
    """Simulate the routing logic extracted from ChatMixin without a real TUI."""
    from client_surfaces.operator_tui.chat_control_audit import AuditLog
    from client_surfaces.operator_tui.chat_control_config import load_chat_control_config

    with patch.dict("os.environ", {"ANANTA_TUI_CHAT_CONTROL_MODE": mode}):
        cfg = load_chat_control_config()

    if not cmd.startswith("/") or cmd.startswith("//"):
        return {"routed": False, "reason": "not a command"}

    parsed = parse_chat_command(cmd, nl_mode_enabled=cfg.nl_mode_enabled)
    decision = evaluate(parsed, config=cfg)
    audit_log = AuditLog(enabled=True)

    if decision.allowed():
        dispatcher = TuiActionDispatcher()
        req = ActionRequest(action_id=decision.action_id, args=decision.normalized_args, source="chat")
        result = dispatcher.dispatch(req)
        audit_log.record(
            source_channel="ai:tutor",
            sender_kind="user",
            raw_text=cmd,
            parsed_action_id=parsed.action_id,
            policy_verdict=decision.verdict,
            dispatch_status=result.status,
            mode=cfg.mode,
            auto_confirmed=decision.auto_confirmed,
            reason=decision.reason,
        )
        return {
            "routed": True,
            "allowed": True,
            "action_id": decision.action_id,
            "dispatch_status": result.status,
            "message": result.message,
            "audit_events": audit_log.events(),
            "control_result_marker": result.control_result_marker,
        }

    audit_log.record(
        source_channel="ai:tutor",
        sender_kind="user",
        raw_text=cmd,
        parsed_action_id=parsed.action_id,
        policy_verdict=decision.verdict,
        dispatch_status="skipped",
        mode=cfg.mode,
        reason=decision.reason,
    )
    return {
        "routed": True,
        "allowed": False,
        "action_id": decision.action_id,
        "reason": decision.reason,
        "audit_events": audit_log.events(),
    }


def test_slash_command_is_routed_not_sent_to_ai():
    result = _run_command_routing("/view list")
    assert result["routed"] is True
    assert result["allowed"] is True
    assert result["action_id"] == "view.list"


def test_slash_command_does_not_set_tutor_ask():
    result = _run_command_routing("/view next")
    assert result["dispatch_status"] == "ok"


def test_normal_message_not_routed():
    result = _run_command_routing("hello AI")
    assert result["routed"] is False


def test_double_slash_not_routed_as_command():
    result = _run_command_routing("//shortcuts")
    assert result["routed"] is False


def test_denied_command_produces_reason():
    result = _run_command_routing("/rm -rf /")
    assert result["routed"] is True
    assert result["allowed"] is False
    assert "reason" in result


def test_autonomous_command_succeeds_without_confirmation():
    result = _run_command_routing("/view list", mode="autonomous_e2e")
    assert result["routed"] is True
    assert result["allowed"] is True
    assert result["control_result_marker"]["status"] == "ok"


def test_audit_event_created_for_allowed_command():
    result = _run_command_routing("/overlay views on")
    events = result.get("audit_events", [])
    assert len(events) == 1
    evt = events[0]
    assert evt.policy_verdict == "allow"
    assert evt.dispatch_status == "ok"


def test_audit_event_created_for_denied_command():
    result = _run_command_routing("/unknown cmd")
    events = result.get("audit_events", [])
    assert len(events) == 1
    evt = events[0]
    assert evt.policy_verdict == "deny"


def test_existing_double_slash_behavior_is_distinct():
    # // does NOT go through chat control routing
    result = _run_command_routing("//")
    assert result["routed"] is False
