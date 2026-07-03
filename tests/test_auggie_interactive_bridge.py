import pytest
from agent.services.augment.auggie_interactive_bridge import (
    AuggieInteractiveBridge, SessionScope, SessionStatus, TurnKind,
    SessionConfigError, SessionSecurityError
)
from agent.services.augment.augment_config import AugmentConfig

def _cfg(enabled=True, approval_required=True, workspace_mode="task_scoped_copy"):
    cfg = AugmentConfig()
    cfg.interactive_bridge.enabled = enabled
    cfg.interactive_bridge.approval_required_for_write = approval_required
    cfg.interactive_bridge.idle_timeout_seconds = 5
    cfg.security.workspace_mode = workspace_mode
    return cfg

def _scope(mode="read_only"):
    return SessionScope(
        workspace_id="ws-1", owner_id="user-1",
        allowed_paths=["src/"], denied_paths=[".env"],
        mode=mode, idle_timeout_seconds=5,
    )

def test_not_enabled_raises():
    bridge = AuggieInteractiveBridge(_cfg(enabled=False))
    with pytest.raises(SessionConfigError):
        bridge.start_session(scope=_scope())

def test_approval_not_required_raises():
    bridge = AuggieInteractiveBridge(_cfg(approval_required=False))
    with pytest.raises(SessionSecurityError):
        bridge.start_session(scope=_scope())

def test_write_proposal_requires_task_scoped_copy():
    bridge = AuggieInteractiveBridge(_cfg(workspace_mode="direct"))
    with pytest.raises(SessionSecurityError):
        bridge.start_session(scope=_scope(mode="write_proposal"))

def test_start_session_creates_record():
    bridge = AuggieInteractiveBridge(_cfg())
    record = bridge.start_session(scope=_scope())
    assert record.status == SessionStatus.RUNNING
    assert record.session_id is not None

def test_session_has_correlation_id():
    bridge = AuggieInteractiveBridge(_cfg())
    record = bridge.start_session(scope=_scope(), correlation_id="corr-123")
    assert record.correlation_id == "corr-123"

def test_session_initial_turn_logged():
    bridge = AuggieInteractiveBridge(_cfg())
    record = bridge.start_session(scope=_scope())
    kinds = [t.kind for t in record.turns]
    assert TurnKind.SYSTEM_EVENT in kinds

def test_send_turn_adds_turns():
    bridge = AuggieInteractiveBridge(_cfg())
    record = bridge.start_session(scope=_scope())
    turn = bridge.send_turn(record.session_id, "analyze auth.py")
    assert turn.kind == TurnKind.AUGGIE_OUTPUT
    # user input + auggie response = 2 new turns
    user_turns = [t for t in record.turns if t.kind == TurnKind.USER_INPUT]
    assert len(user_turns) == 1

def test_send_turn_redacts_secrets():
    bridge = AuggieInteractiveBridge(_cfg())
    record = bridge.start_session(scope=_scope())
    bridge.send_turn(record.session_id, "token=mysecrettoken123")
    user_turns = [t for t in record.turns if t.kind == TurnKind.USER_INPUT]
    assert "mysecrettoken123" not in user_turns[0].content_redacted
    assert "[REDACTED]" in user_turns[0].content_redacted

def test_end_session_marks_completed():
    bridge = AuggieInteractiveBridge(_cfg())
    record = bridge.start_session(scope=_scope())
    record2, proposal = bridge.end_session(record.session_id)
    assert record2.status == SessionStatus.COMPLETED
    assert record2.ended_at is not None

def test_end_session_write_proposal_creates_proposal():
    bridge = AuggieInteractiveBridge(_cfg())
    record = bridge.start_session(scope=_scope(mode="write_proposal"))
    _, proposal = bridge.end_session(record.session_id)
    assert proposal is not None
    assert proposal.approval_required is True

def test_end_session_read_only_no_proposal():
    bridge = AuggieInteractiveBridge(_cfg())
    record = bridge.start_session(scope=_scope(mode="read_only"))
    _, proposal = bridge.end_session(record.session_id)
    assert proposal is None

def test_kill_session():
    bridge = AuggieInteractiveBridge(_cfg())
    record = bridge.start_session(scope=_scope())
    killed = bridge.kill_session(record.session_id, reason="test")
    assert killed.status == SessionStatus.KILLED

def test_idle_timeout_detection():
    import time
    bridge = AuggieInteractiveBridge(_cfg())
    record = bridge.start_session(scope=_scope())
    # Manually backdate last turn
    record.turns[-1].timestamp = time.time() - 100
    timed_out = bridge.check_idle_timeout(record.session_id)
    assert timed_out is True
    assert record.status == SessionStatus.IDLE_TIMEOUT

def test_idle_timeout_not_triggered_for_recent_session():
    bridge = AuggieInteractiveBridge(_cfg())
    record = bridge.start_session(scope=_scope())
    # Recent session — should NOT timeout
    timed_out = bridge.check_idle_timeout(record.session_id)
    assert timed_out is False

def test_get_tracking_info_structure():
    bridge = AuggieInteractiveBridge(_cfg())
    record = bridge.start_session(scope=_scope())
    info = bridge.get_tracking_info(record.session_id)
    assert "session_id" in info
    assert "status" in info
    assert "turn_count" in info
    assert "scope_paths" in info

def test_get_redacted_transcript():
    bridge = AuggieInteractiveBridge(_cfg())
    record = bridge.start_session(scope=_scope())
    bridge.send_turn(record.session_id, "hello")
    transcript = bridge.get_redacted_transcript(record.session_id)
    assert isinstance(transcript, list)
    assert all("content" in t for t in transcript)

def test_change_proposal_linked_in_session():
    bridge = AuggieInteractiveBridge(_cfg())
    record = bridge.start_session(scope=_scope(mode="write_proposal"))
    _, proposal = bridge.end_session(record.session_id)
    assert record.change_proposal_ref == proposal.proposal_id

def test_send_turn_on_ended_session_raises():
    bridge = AuggieInteractiveBridge(_cfg())
    record = bridge.start_session(scope=_scope())
    bridge.end_session(record.session_id)
    with pytest.raises(SessionConfigError):
        bridge.send_turn(record.session_id, "too late")

def test_unknown_session_raises():
    bridge = AuggieInteractiveBridge(_cfg())
    with pytest.raises(KeyError):
        bridge.end_session("nonexistent")
