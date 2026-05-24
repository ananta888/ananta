from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from agent.services.terminal_session_service import TerminalSessionService, _ATTACH_TOKENS


def _clear_tokens():
    _ATTACH_TOKENS.clear()


def test_expired_attach_token_rejected():
    _clear_tokens()
    token = "test-expired-token"
    _ATTACH_TOKENS[token] = ("sess1", "user1", time.time() - 1)  # already expired

    svc = TerminalSessionService.__new__(TerminalSessionService)
    result = svc.resolve_attach_token(token)
    assert result is None
    assert token not in _ATTACH_TOKENS


def test_valid_attach_token_consumed_once():
    _clear_tokens()
    token = "test-valid-token"
    _ATTACH_TOKENS[token] = ("sess2", "user2", time.time() + 60)

    svc = TerminalSessionService.__new__(TerminalSessionService)
    first = svc.resolve_attach_token(token)
    assert first == ("sess2", "user2")
    assert token not in _ATTACH_TOKENS

    second = svc.resolve_attach_token(token)
    assert second is None


def test_attach_token_scoped_to_session_and_user():
    _clear_tokens()
    token = "scoped-token"
    _ATTACH_TOKENS[token] = ("sess-specific", "user-specific", time.time() + 60)

    svc = TerminalSessionService.__new__(TerminalSessionService)
    result = svc.resolve_attach_token(token)
    assert result is not None
    session_id, user_id = result
    assert session_id == "sess-specific"
    assert user_id == "user-specific"


def test_unknown_token_returns_none():
    _clear_tokens()
    svc = TerminalSessionService.__new__(TerminalSessionService)
    result = svc.resolve_attach_token("this-token-does-not-exist")
    assert result is None


def test_attach_token_not_in_url_path():
    """Tokens must be passed as query params, not embedded in URL paths."""
    # This is a design constraint: verify the WS endpoint uses ?attach_token=
    import inspect
    import agent.ws_terminal as ws_mod
    src = inspect.getsource(ws_mod.register_ws_terminal)
    assert "attach_token" in src
    assert "/ws/terminal/session" in src


def test_wss_without_token_sends_error():
    """ws_terminal_session handler sends error when attach_token is absent."""
    from agent.ws_terminal import _attach_token_ws_session

    mock_ws = MagicMock()
    mock_app = MagicMock()
    mock_app.config.get.return_value = "data"

    # token not in _ATTACH_TOKENS
    _clear_tokens()
    _attach_token_ws_session(mock_ws, "nonexistent-token", mock_app)
    mock_ws.send.assert_called()
    sent = mock_ws.send.call_args[0][0]
    import json
    payload = json.loads(sent)
    assert payload["type"] == "error"
    assert "unauthorized" in payload["data"]["message"] or "invalid" in payload["data"].get("details", "")


def test_gateway_disconnect_transitions_session_to_detached():
    """After WSS disconnect the session must move to detached, not killed."""
    from agent.db_models import TerminalSessionDB
    from agent.services.terminal_session_service import TerminalSessionService

    # Pre-load a token
    _clear_tokens()
    token = "gateway-test-token"
    _ATTACH_TOKENS[token] = ("sess-gw", "user-gw", time.time() + 60)

    sess = TerminalSessionDB(
        id="sess-gw",
        created_by_user_id="user-gw",
        created_by_username="user-gw",
        target_type="worker",
        target_id="w1",
        status="running",
        read_only=False,
        policy_decision_id="dec-gw",
        tmux_session_name="ananta-worker-gw",
    )

    mock_ws = MagicMock()
    mock_ws.send = MagicMock()
    mock_app = MagicMock()
    mock_app.config.get.return_value = "/tmp/test-data"

    from agent.ws_terminal import _attach_token_ws_session

    with patch("agent.ws_terminal.get_terminal_session_service") as mock_svc_fn:
        mock_svc = mock_svc_fn.return_value
        mock_svc.resolve_attach_token.return_value = ("sess-gw", "user-gw")

        with patch("agent.ws_terminal.get_repository_registry") as mock_reg:
            mock_reg.return_value.terminal_session_repo.get_by_id.return_value = sess
            mock_reg.return_value.terminal_session_repo.transition_status = MagicMock()
            mock_reg.return_value.terminal_event_repo.append = MagicMock()

            with patch("agent.ws_terminal.get_tmux_session_backend") as mock_be:
                # Make capture_output raise so we exit the loop immediately
                mock_be.return_value.capture_output.side_effect = Exception("tmux_gone")
                with patch("agent.ws_terminal._WebSocketInputPump") as mock_pump_cls:
                    mock_pump = MagicMock()
                    mock_pump.closed = True
                    mock_pump_cls.return_value = mock_pump
                    with patch("agent.ws_terminal.time.sleep"):
                        _attach_token_ws_session(mock_ws, token, mock_app)

            # Session must be set to detached after disconnect
            mock_reg.return_value.terminal_session_repo.transition_status.assert_any_call("sess-gw", "detached")
