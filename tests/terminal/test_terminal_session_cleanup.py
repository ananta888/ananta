from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from agent.services.terminal_cleanup_service import TerminalCleanupService


def _make_session(session_id: str, status: str = "running", expires_at: float | None = None, idle_expires_at: float | None = None):
    from agent.db_models import TerminalSessionDB
    return TerminalSessionDB(
        id=session_id,
        created_by_user_id="u1",
        created_by_username="u1",
        target_type="worker",
        target_id="w1",
        status=status,
        read_only=False,
        policy_decision_id="dec1",
        tmux_session_name=f"ananta-{session_id}",
        expires_at=expires_at,
        idle_expires_at=idle_expires_at,
    )


def test_cleanup_expires_max_lifetime():
    svc = TerminalCleanupService()
    past = time.time() - 1
    session = _make_session("s1", expires_at=past)

    with patch("agent.services.terminal_cleanup_service.get_repository_registry") as mock_reg:
        mock_reg.return_value.terminal_session_repo.list_all.return_value = [session]
        mock_reg.return_value.terminal_session_repo.transition_status = MagicMock()
        mock_reg.return_value.terminal_event_repo.append = MagicMock()
        with patch("agent.services.terminal_cleanup_service.get_tmux_session_backend") as mock_be:
            mock_be.return_value.kill_session = MagicMock()
            with patch("agent.services.terminal_cleanup_service.log_audit"):
                result = svc.run_cleanup_tick()

    assert "s1" in result["expired"]
    mock_reg.return_value.terminal_session_repo.transition_status.assert_called_once_with("s1", "expired")


def test_cleanup_expires_idle_timeout():
    svc = TerminalCleanupService()
    past = time.time() - 1
    session = _make_session("s2", idle_expires_at=past)

    with patch("agent.services.terminal_cleanup_service.get_repository_registry") as mock_reg:
        mock_reg.return_value.terminal_session_repo.list_all.return_value = [session]
        mock_reg.return_value.terminal_session_repo.transition_status = MagicMock()
        mock_reg.return_value.terminal_event_repo.append = MagicMock()
        with patch("agent.services.terminal_cleanup_service.get_tmux_session_backend") as mock_be:
            mock_be.return_value.kill_session = MagicMock()
            with patch("agent.services.terminal_cleanup_service.log_audit"):
                result = svc.run_cleanup_tick()

    assert "s2" in result["expired"]


def test_cleanup_skips_already_killed():
    svc = TerminalCleanupService()
    past = time.time() - 1
    session = _make_session("s3", status="killed", expires_at=past)

    with patch("agent.services.terminal_cleanup_service.get_repository_registry") as mock_reg:
        mock_reg.return_value.terminal_session_repo.list_all.return_value = [session]
        result = svc.run_cleanup_tick()

    assert "s3" not in result["expired"]


def test_cleanup_does_not_kill_unrelated_tmux():
    svc = TerminalCleanupService()
    future = time.time() + 9999
    session = _make_session("s4", expires_at=future, idle_expires_at=future)

    with patch("agent.services.terminal_cleanup_service.get_repository_registry") as mock_reg:
        mock_reg.return_value.terminal_session_repo.list_all.return_value = [session]
        with patch("agent.services.terminal_cleanup_service.get_tmux_session_backend") as mock_be:
            mock_be.return_value.kill_session = MagicMock()
            result = svc.run_cleanup_tick()

    assert "s4" not in result["expired"]
    mock_be.return_value.kill_session.assert_not_called()


def test_cleanup_expired_session_cannot_receive_input():
    from agent.services.terminal_session_service import TerminalSessionService
    from agent.db_models import TerminalSessionDB

    svc = TerminalSessionService.__new__(TerminalSessionService)
    expired_sess = TerminalSessionDB(
        id="s5",
        created_by_user_id="u1",
        created_by_username="u1",
        target_type="worker",
        target_id="w1",
        status="expired",
        read_only=False,
        policy_decision_id="dec1",
    )
    with patch.object(svc, "get_session", return_value=expired_sess):
        result = svc.send_input("s5", text="ls", user_ctx={"sub": "u1"}, cfg={})
    assert not result["ok"]
    assert result["reason_code"] == "terminal_session_not_active"
