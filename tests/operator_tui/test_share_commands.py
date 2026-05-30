"""SS03.02: Tests für :share TUI-Kommandos."""
from __future__ import annotations

import pytest
from unittest.mock import patch

from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState, OperatorMode


def _base_state(**kw) -> OperatorState:
    return OperatorState(
        endpoint="http://localhost:5000",
        section_id="dashboard",
        selected_index=0,
        mode=OperatorMode.NORMAL,
        **kw,
    )


def test_share_status_navigates_to_share_section():
    state = _base_state()
    result = execute_command(":share status", state)
    assert result.state.section_id == "share"


def test_share_help_returns_hint():
    state = _base_state()
    result = execute_command(":share help", state)
    hint = result.state.status_message or result.message
    assert "share" in hint.lower()
    assert "create" in hint.lower()


def test_share_create_sets_pending_action():
    state = _base_state()
    result = execute_command(":share create My Session", state)
    game = result.state.header_logo_game or {}
    assert game.get("share_pending_action", {}).get("action") == "create"
    assert "My Session" in game.get("share_pending_action", {}).get("title", "")
    assert result.state.section_id == "share"


def test_share_create_default_title():
    state = _base_state()
    result = execute_command(":share create", state)
    game = result.state.header_logo_game or {}
    assert game.get("share_pending_action", {}).get("title") == "Shared Session"


def test_share_invite_no_active_session():
    state = _base_state()
    result = execute_command(":share invite", state)
    assert "Keine aktive" in result.message or "invite" in result.message.lower()


def test_share_invite_with_active_session():
    state = _base_state(header_logo_game={"share_active_session": {"invite_code": "TESTCODE12"}})
    result = execute_command(":share invite", state)
    assert "TESTCODE12" in result.message


def test_share_join_sets_pending_action():
    state = _base_state()
    result = execute_command(":share join ABCD1234", state)
    game = result.state.header_logo_game or {}
    action = game.get("share_pending_action", {})
    assert action.get("action") == "join"
    assert action.get("invite_code") == "ABCD1234"


def test_share_key_generate_creates_key(tmp_path):
    with patch("client_surfaces.operator_tui.device_keys.get_device_key_manager") as mock_mgr:
        mock_instance = mock_mgr.return_value
        mock_instance.key_exists.return_value = False
        mock_instance.generate_key.return_value = {"fingerprint": "aa:bb:cc:dd:ee:ff:11:22"}
        state = _base_state()
        result = execute_command(":share key generate", state)
        assert "aa:bb" in result.message or "erstellt" in result.message.lower()


def test_share_key_generate_already_exists():
    with patch("client_surfaces.operator_tui.device_keys.get_device_key_manager") as mock_mgr:
        mock_instance = mock_mgr.return_value
        mock_instance.key_exists.return_value = True
        state = _base_state()
        result = execute_command(":share key generate", state)
        assert "existiert" in result.message.lower() or "rotate" in result.message.lower()


def test_share_key_show_displays_fingerprint():
    with patch("client_surfaces.operator_tui.device_keys.get_device_key_manager") as mock_mgr:
        from client_surfaces.operator_tui.device_keys import DeviceKeyManager
        mock_instance = mock_mgr.return_value
        mock_instance.get_public_info.return_value = {"fingerprint": "12:34:56:78:9a:bc:de:f0"}
        state = _base_state()
        result = execute_command(":share key show", state)
        assert "12:34:56" in result.message


def test_share_key_rotate_updates_fingerprint():
    with patch("client_surfaces.operator_tui.device_keys.get_device_key_manager") as mock_mgr:
        mock_instance = mock_mgr.return_value
        mock_instance.rotate_key.return_value = {"fingerprint": "new:fp:aa:bb:cc:dd:ee:ff"}
        state = _base_state()
        result = execute_command(":share key rotate", state)
        assert "new:fp" in result.message or "rotiert" in result.message.lower()


def test_share_view_on_no_session():
    state = _base_state()
    result = execute_command(":share view on", state)
    assert not result.handled or "Keine" in result.message


def test_share_view_on_with_session():
    state = _base_state(header_logo_game={"share_active_session": {"id": "sess-1"}})
    result = execute_command(":share view on", state)
    game = result.state.header_logo_game or {}
    action = game.get("share_pending_action", {})
    assert action.get("action") == "set_view"
    assert action.get("view_tui") is True


def test_share_view_invalid_subcommand():
    state = _base_state()
    result = execute_command(":share view invalid", state)
    assert not result.handled


def test_share_stop_sets_pending_action():
    state = _base_state()
    result = execute_command(":share stop", state)
    game = result.state.header_logo_game or {}
    assert game.get("share_pending_action", {}).get("action") == "stop"


def test_share_unknown_subcommand_returns_help():
    state = _base_state()
    result = execute_command(":share foobar", state)
    assert not result.handled
