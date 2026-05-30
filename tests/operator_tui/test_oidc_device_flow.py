"""Tests für OIDC Device Flow (offline/mocked)."""
from __future__ import annotations

import time
import pytest
from unittest.mock import patch, MagicMock

from client_surfaces.operator_tui.oidc_device_flow import (
    DeviceFlowState,
    poll_device_flow,
    DeviceFlowPoller,
    status_lines,
)


def _make_state(**kw) -> DeviceFlowState:
    defaults = dict(
        status="waiting",
        device_code="dev-code-123",
        user_code="ABCD-1234",
        verification_uri="https://keycloak.ananta.de/realms/ananta/device",
        expires_at=time.time() + 600,
        interval=5.0,
        issuer="https://keycloak.ananta.de/realms/ananta",
        client_id="ananta-tui",
    )
    defaults.update(kw)
    return DeviceFlowState(**defaults)


def _mock_token_response(token: str):
    import io, json
    resp = MagicMock()
    resp.read.return_value = json.dumps({"access_token": token}).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _mock_pending_response():
    import urllib.error, json
    exc = urllib.error.HTTPError(url="", code=400, msg="", hdrs=None, fp=None)
    exc.read = lambda: json.dumps({"error": "authorization_pending"}).encode()
    return exc


def test_poll_returns_polling_on_pending():
    state = _make_state()
    with patch("urllib.request.urlopen", side_effect=_mock_pending_response()):
        new_state = poll_device_flow(state)
    assert new_state.status == "polling"
    assert new_state.error == ""


def test_poll_returns_done_on_token():
    state = _make_state()
    with patch("urllib.request.urlopen", return_value=_mock_token_response("my-access-token")):
        new_state = poll_device_flow(state)
    assert new_state.status == "done"
    assert new_state.access_token == "my-access-token"


def test_poll_returns_expired_when_past_expiry():
    state = _make_state(expires_at=time.time() - 1)
    new_state = poll_device_flow(state)
    assert new_state.status == "expired"


def test_poll_returns_error_on_access_denied():
    import urllib.error, json
    state = _make_state()
    exc = urllib.error.HTTPError(url="", code=400, msg="", hdrs=None, fp=None)
    exc.read = lambda: json.dumps({"error": "access_denied"}).encode()
    with patch("urllib.request.urlopen", side_effect=exc):
        new_state = poll_device_flow(state)
    assert new_state.status == "error"
    assert new_state.error == "access_denied"


def test_poll_increments_interval_on_slow_down():
    import urllib.error, json
    state = _make_state(interval=5.0)
    exc = urllib.error.HTTPError(url="", code=400, msg="", hdrs=None, fp=None)
    exc.read = lambda: json.dumps({"error": "slow_down"}).encode()
    with patch("urllib.request.urlopen", side_effect=exc):
        new_state = poll_device_flow(state)
    assert new_state.interval > state.interval


def test_status_lines_waiting():
    state = _make_state(status="waiting")
    lines = status_lines(state)
    combined = "\n".join(lines)
    assert "ABCD-1234" in combined
    assert "keycloak.ananta.de" in combined


def test_status_lines_done():
    state = _make_state(status="done")
    lines = status_lines(state)
    assert any("erfolgreich" in l or "✓" in l for l in lines)


def test_status_lines_error():
    state = _make_state(status="error", error="access_denied")
    lines = status_lines(state)
    assert any("access_denied" in l or "fehlgeschlagen" in l for l in lines)


def test_poller_clear():
    poller = DeviceFlowPoller()
    poller._state = _make_state()
    poller.clear()
    assert poller.get_state() is None


def test_oidc_command_login_no_issuer():
    """Ohne konfigurierten Issuer gibt :oidc login eine Fehlermeldung."""
    from client_surfaces.operator_tui.commands import execute_command
    from client_surfaces.operator_tui.models import OperatorState, OperatorMode
    state = OperatorState(endpoint="http://localhost:5000", section_id="dashboard", selected_index=0, mode=OperatorMode.NORMAL)
    with patch("client_surfaces.operator_tui.network_profile.oidc_issuer", return_value=""):
        result = execute_command(":oidc login", state)
    assert not result.handled or "Issuer" in result.state.status_message or "OIDC" in result.state.status_message


def test_oidc_command_logout_clears_token():
    from client_surfaces.operator_tui.commands import execute_command
    from client_surfaces.operator_tui.models import OperatorState, OperatorMode
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="share",
        selected_index=0,
        mode=OperatorMode.NORMAL,
        header_logo_game={"oidc_token": "some-token"},
    )
    result = execute_command(":oidc logout", state)
    game = result.state.header_logo_game or {}
    assert not game.get("oidc_token")


def test_oidc_command_status_with_token():
    from client_surfaces.operator_tui.commands import execute_command
    from client_surfaces.operator_tui.models import OperatorState, OperatorMode
    import base64, json
    payload = base64.b64encode(json.dumps({"sub": "u1", "preferred_username": "testuser", "iss": "https://kc"}).encode()).decode()
    fake_token = f"header.{payload}.sig"
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="share",
        selected_index=0,
        mode=OperatorMode.NORMAL,
        header_logo_game={"oidc_token": fake_token},
    )
    result = execute_command(":oidc status", state)
    assert "testuser" in result.state.status_message or "eingeloggt" in result.state.status_message
