from __future__ import annotations

from client_surfaces.operator_tui.chat_control_audit import AuditLog
from client_surfaces.operator_tui.chat_control_config import ChatControlConfig
from client_surfaces.operator_tui.chat_control_parser import parse_chat_command
from client_surfaces.operator_tui.chat_control_policy import evaluate
from client_surfaces.operator_tui.tui_action_dispatcher import ActionRequest, TuiActionDispatcher


def _run_with_audit(cmd: str, mode: str = "autonomous_e2e") -> tuple[dict, AuditLog]:
    log = AuditLog(enabled=True)
    cfg = ChatControlConfig(mode=mode)
    parsed = parse_chat_command(cmd)
    decision = evaluate(parsed, config=cfg)
    if decision.allowed():
        d = TuiActionDispatcher()
        result = d.dispatch(ActionRequest(action_id=decision.action_id, args=decision.normalized_args, source="test"))
        log.record(
            source_channel="test",
            sender_kind="test",
            raw_text=cmd,
            parsed_action_id=parsed.action_id,
            policy_verdict=decision.verdict,
            dispatch_status=result.status,
            mode=cfg.mode,
            auto_confirmed=decision.auto_confirmed,
            reason=decision.reason,
            extra={"control_result_marker": result.control_result_marker},
        )
    else:
        log.record(
            source_channel="test",
            sender_kind="test",
            raw_text=cmd,
            parsed_action_id=parsed.action_id,
            policy_verdict=decision.verdict,
            dispatch_status="skipped",
            mode=cfg.mode,
            reason=decision.reason,
        )
    return {"verdict": decision.verdict}, log


def test_successful_command_produces_audit_event():
    _, log = _run_with_audit("/view list")
    events = log.events()
    assert len(events) == 1
    assert events[0].policy_verdict == "allow"
    assert events[0].dispatch_status == "ok"
    assert events[0].mode == "autonomous_e2e"


def test_denied_command_produces_audit_event():
    _, log = _run_with_audit("/unknown cmd")
    events = log.events()
    assert len(events) == 1
    assert events[0].policy_verdict == "deny"
    assert events[0].dispatch_status == "skipped"


def test_auto_confirmed_flagged_in_audit():
    _, log = _run_with_audit("/view list", mode="autonomous_e2e")
    assert log.events()[0].auto_confirmed is True


def test_audit_does_not_store_raw_text():
    _, log = _run_with_audit("/view list")
    evt = log.events()[0]
    assert len(evt.raw_text_hash) == 12  # only SHA256 prefix, not raw text


def test_audit_can_be_captured_in_memory():
    log = AuditLog(enabled=True)
    assert log.events() == []
    log.record(
        source_channel="test", sender_kind="test", raw_text="/test",
        parsed_action_id="test", policy_verdict="deny", dispatch_status="skipped",
        mode="autonomous_e2e", reason="test reason",
    )
    assert len(log.events()) == 1


def test_audit_disabled_stores_nothing():
    log = AuditLog(enabled=False)
    log.record(
        source_channel="test", sender_kind="test", raw_text="/test",
        parsed_action_id="test", policy_verdict="allow", dispatch_status="ok",
        mode="autonomous_e2e", reason="",
    )
    assert log.events() == []
