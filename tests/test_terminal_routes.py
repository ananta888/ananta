from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_user_ctx():
    return {"sub": "testuser", "role": "user", "username": "testuser"}


@pytest.fixture
def mock_admin_ctx():
    return {"sub": "admin", "role": "admin", "username": "admin"}


def test_create_session_missing_target_type():
    from agent.services.terminal_session_service import TerminalSessionService
    from agent.services.terminal_policy_service import TerminalPolicyDecision

    svc = TerminalSessionService.__new__(TerminalSessionService)

    with patch("agent.services.terminal_session_service.get_terminal_policy_service") as mock_policy:
        decision = TerminalPolicyDecision(
            allow=False,
            reason_code="terminal_permission_denied",
            decision_id="x",
            policy_version="v1",
            matched_rule_id=None,
            permission="terminal.worker.create",
        )
        mock_policy.return_value.evaluate.return_value = decision

        with patch("agent.services.terminal_session_service.get_repository_registry") as mock_reg:
            mock_reg.return_value.terminal_event_repo.append = MagicMock()
            result = svc.create_session(
                user_ctx={"sub": "u", "role": "viewer"},
                target_type="worker",
                target_id="w1",
                cfg={},
            )
        assert not result["ok"]
        assert result["status"] == "forbidden"


def test_create_session_refused_hub_without_permission():
    from agent.services.terminal_policy_service import TerminalPolicyService

    svc = TerminalPolicyService()
    decision = svc.evaluate(
        user_ctx={"sub": "u", "role": "admin"},
        operation="create",
        target_type="hub",
        target_id="hub",
        cfg={},
    )
    assert not decision.allow
    assert decision.reason_code == "terminal_hub_access_denied_default"


def test_create_session_refused_hub_as_worker_without_permission():
    from agent.services.terminal_policy_service import TerminalPolicyService

    svc = TerminalPolicyService()
    decision = svc.evaluate(
        user_ctx={"sub": "u", "role": "user"},
        operation="create",
        target_type="hub_as_worker",
        target_id="hub",
        cfg={},
    )
    assert not decision.allow


def test_get_output_read_only_session_allowed():
    from agent.services.terminal_policy_service import TerminalPolicyService

    svc = TerminalPolicyService()
    decision = svc.evaluate(
        user_ctx={"sub": "u", "role": "user"},
        operation="read",
        target_type="worker",
        target_id="w1",
        cfg={},
    )
    assert decision.allow


def test_send_input_read_only_blocked():
    from agent.services.terminal_session_service import TerminalSessionService
    from agent.db_models import TerminalSessionDB

    svc = TerminalSessionService.__new__(TerminalSessionService)

    ro_session = TerminalSessionDB(
        id="sess1",
        created_by_user_id="testuser",
        created_by_username="testuser",
        target_type="worker",
        target_id="w1",
        status="running",
        read_only=True,
        policy_decision_id="dec1",
    )

    with patch.object(svc, "get_session", return_value=ro_session):
        result = svc.send_input("sess1", text="ls", user_ctx={"sub": "testuser"}, cfg={})

    assert not result["ok"]
    assert result["reason_code"] == "terminal_session_read_only"


def test_kill_session_emits_audit_event():
    from agent.services.terminal_session_service import TerminalSessionService
    from agent.db_models import TerminalSessionDB
    from agent.services.terminal_policy_service import TerminalPolicyDecision

    svc = TerminalSessionService.__new__(TerminalSessionService)

    sess = TerminalSessionDB(
        id="sess2",
        created_by_user_id="testuser",
        created_by_username="testuser",
        target_type="worker",
        target_id="w1",
        status="running",
        read_only=False,
        tmux_session_name="ananta-worker-abc123",
        policy_decision_id="dec2",
    )

    allow_decision = TerminalPolicyDecision(
        allow=True, reason_code="terminal_permission_granted",
        decision_id="d1", policy_version="v1",
        matched_rule_id="worker.kill.allow", permission="terminal.worker.kill",
    )

    with patch.object(svc, "get_session", return_value=sess):
        with patch("agent.services.terminal_session_service.get_terminal_policy_service") as mock_policy:
            mock_policy.return_value.evaluate.return_value = allow_decision
            with patch("agent.services.terminal_session_service.get_repository_registry") as mock_reg:
                mock_reg.return_value.terminal_session_repo.transition_status = MagicMock()
                mock_reg.return_value.terminal_event_repo.append = MagicMock()
                with patch("agent.services.terminal_session_service.get_tmux_session_backend") as mock_be:
                    mock_be.return_value.kill_session = MagicMock()
                    with patch("agent.services.terminal_session_service.log_audit"):
                        result = svc.kill_session("sess2", user_ctx={"sub": "testuser", "role": "user"}, cfg={})

    assert result["ok"]
    mock_reg.return_value.terminal_event_repo.append.assert_called()


def test_attach_token_single_use():
    from agent.services.terminal_session_service import TerminalSessionService, _ATTACH_TOKENS
    from agent.db_models import TerminalSessionDB
    from agent.services.terminal_policy_service import TerminalPolicyDecision

    svc = TerminalSessionService.__new__(TerminalSessionService)

    sess = TerminalSessionDB(
        id="sess3",
        created_by_user_id="testuser",
        created_by_username="testuser",
        target_type="worker",
        target_id="w1",
        status="running",
        read_only=False,
        policy_decision_id="dec3",
    )

    allow_decision = TerminalPolicyDecision(
        allow=True, reason_code="terminal_permission_granted",
        decision_id="d1", policy_version="v1",
        matched_rule_id="worker.attach.allow", permission="terminal.worker.attach",
    )

    with patch.object(svc, "get_session", return_value=sess):
        with patch("agent.services.terminal_session_service.get_terminal_policy_service") as mock_policy:
            mock_policy.return_value.evaluate.return_value = allow_decision
            with patch("agent.services.terminal_session_service.get_repository_registry") as mock_reg:
                mock_reg.return_value.terminal_event_repo.append = MagicMock()
                result = svc.generate_attach_token("sess3", user_ctx={"sub": "testuser", "role": "user"}, cfg={})

    assert result["ok"]
    token = result["attach_token"]
    assert token in _ATTACH_TOKENS

    # first resolve consumes it
    resolved = svc.resolve_attach_token(token)
    assert resolved is not None
    assert resolved[0] == "sess3"

    # second resolve returns None — single-use
    resolved2 = svc.resolve_attach_token(token)
    assert resolved2 is None
