"""Tests: :- Kommandos in Snake-Mode und Share-Section Maus-Bedienung."""

from __future__ import annotations

import re

import pytest

from client_surfaces.operator_tui.share_menu import (
    build_share_section_lines,
    extract_click_command,
)

_ANSI = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-9;?]*[ -/]*[@-~])")


@pytest.fixture(autouse=True)
def _use_local_profile_by_default(monkeypatch):
    monkeypatch.setenv("ANANTA_NETWORK_PROFILE", "local")


def _strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


# ── extract_click_command ─────────────────────────────────────────────────────


def test_extract_click_command_finds_btn():
    line = "  \x1b[36m[▶ :oidc login]\x1b[0m \x1b[90mOIDC-Login starten\x1b[0m"
    assert extract_click_command(line) == ":oidc login"


def test_extract_click_command_share_create():
    line = "  [▶ :share create] neue Session erstellen"
    assert extract_click_command(line) == ":share create"


def test_extract_click_command_no_btn():
    assert extract_click_command("  Netzwerkprofil: local") is None
    assert extract_click_command("  OIDC: nicht eingeloggt") is None
    assert extract_click_command("") is None


def test_extract_click_command_with_subcommand():
    line = "  [▶ :share key generate] lokalen Device-Key erstellen"
    assert extract_click_command(line) == ":share key generate"


def test_extract_click_command_view_on():
    line = "  [▶ :share view on] TUI-View freigeben"
    assert extract_click_command(line) == ":share view on"


def test_extract_click_command_uses_click_column_for_multiple_buttons():
    line = "  [▶ :share view on] TUI-View freigeben  [▶ :share view off] View sperren"
    assert extract_click_command(line, x=line.index(":share view on")) == ":share view on"
    assert extract_click_command(line, x=line.index("TUI-View")) == ":share view on"
    assert extract_click_command(line, x=line.index(":share view off")) == ":share view off"
    assert extract_click_command(line, x=line.index("View sperren")) == ":share view off"
    assert extract_click_command(line, x=0) is None


# ── share_menu enthält klickbare Buttons ──────────────────────────────────────


def _plain_lines(payload=None, **kw) -> list[str]:
    import re

    ansi = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-9;?]*[ -/]*[@-~])")
    lines = build_share_section_lines(payload or {}, **kw)
    return [ansi.sub("", line) for line in lines]


def test_share_menu_has_oidc_login_button_when_not_logged_in():
    lines = _plain_lines({"oidc_status": {}})
    btn_lines = [line for line in lines if "[▶ :oidc login]" in line]
    assert len(btn_lines) >= 1


def test_share_menu_has_key_generate_button_when_no_key(tmp_path):
    from unittest.mock import patch

    from client_surfaces.operator_tui.device_keys import DeviceKeyManager

    mgr = DeviceKeyManager(tmp_path / "nokeys")
    with patch("client_surfaces.operator_tui.share_menu.get_device_key_manager", return_value=mgr):
        lines = _plain_lines({})
    btn_lines = [line for line in lines if "[▶ :share key generate]" in line]
    assert len(btn_lines) >= 1


def test_share_menu_has_create_button_when_no_sessions():
    lines = _plain_lines({"sessions": []})
    btn_lines = [line for line in lines if "[▶ :share create]" in line]
    assert len(btn_lines) >= 1


def test_share_menu_has_join_button_when_no_sessions():
    lines = _plain_lines({"sessions": []})
    btn_lines = [line for line in lines if ":share join" in line]
    assert len(btn_lines) >= 1


def test_public_share_menu_hides_unsupported_strict_create_and_join(monkeypatch):
    monkeypatch.setenv("ANANTA_NETWORK_PROFILE", "public-ananta")

    lines = _plain_lines({"sessions": []})
    rendered = "\n".join(lines)

    assert "strict_pair_adapter_required" in rendered
    assert "[▶ :share create]" not in rendered
    assert ":share join" not in rendered


def test_public_share_actions_fail_before_using_the_legacy_tui_adapter(monkeypatch):
    from client_surfaces.operator_tui._snake_tick_share import (
        share_action_create,
        share_action_join,
    )

    monkeypatch.setenv("ANANTA_NETWORK_PROFILE", "public-ananta")
    create_game: dict = {}
    join_game: dict = {}

    share_action_create(None, create_game, "oidc-token", "", "", "Blocked")
    share_action_join(None, join_game, "oidc-token", "", "", "INVITECODE")

    assert create_game["share_status_message"].startswith("strict_pair_adapter_required:")
    assert join_game["share_status_message"].startswith("strict_pair_adapter_required:")


def test_public_list_view_and_stop_without_oidc_never_fall_back_to_hub(monkeypatch):
    import urllib.request

    from client_surfaces.operator_tui import share_client
    from client_surfaces.operator_tui._snake_tick_share import (
        share_action_list,
        share_action_set_view,
        share_action_stop,
    )

    class RecordingTui:
        def __init__(self):
            self.hub_resolution_attempts: list[tuple[str, str]] = []

        def _resolve_hub_jwt(self, hub_raw: str, endpoint: str) -> str:
            self.hub_resolution_attempts.append((hub_raw, endpoint))
            return "hub-token"

        def _append_share_audit(self, _game: dict, _text: str) -> None:
            pytest.fail("a failed-closed action must not append a success audit entry")

    network_calls: list[tuple] = []

    def record_network_call(*args, **kwargs):
        network_calls.append((args, kwargs))
        raise AssertionError("public action unexpectedly reached a network client")

    monkeypatch.setenv("ANANTA_NETWORK_PROFILE", "public-ananta")
    monkeypatch.setattr(share_client, "list_sessions", record_network_call)
    monkeypatch.setattr(share_client, "list_hub_sessions", record_network_call)
    monkeypatch.setattr(share_client, "update_session_permissions", record_network_call)
    monkeypatch.setattr(share_client, "revoke_session", record_network_call)
    monkeypatch.setattr(urllib.request, "urlopen", record_network_call)

    tui = RecordingTui()
    session_id = "public-session-id"
    list_game: dict = {}
    view_game: dict = {}
    stop_game: dict = {"share_active_session": {"id": session_id}}

    share_action_list(tui, list_game, "", "hub-secret", "https://hub.invalid")
    share_action_set_view(
        tui,
        view_game,
        "",
        "hub-secret",
        "https://hub.invalid",
        session_id,
        True,
    )
    share_action_stop(
        tui,
        stop_game,
        "",
        "hub-secret",
        "https://hub.invalid",
        session_id,
    )

    for game in (list_game, view_game, stop_game):
        assert game["share_status_message"].startswith("oidc_auth_required:")
    assert stop_game["share_active_session"]["id"] == session_id
    assert tui.hub_resolution_attempts == []
    assert network_calls == []


def test_public_view_requires_strict_pair_capability_before_network_call(monkeypatch):
    from client_surfaces.operator_tui import network_profile, share_client
    from client_surfaces.operator_tui._snake_tick_share import share_action_set_view

    class NoHubTui:
        def _resolve_hub_jwt(self, _hub_raw: str, _endpoint: str) -> str:
            pytest.fail("a public session identifier must never be sent to the Hub")

    def unexpected_public_update(*_args, **_kwargs):
        pytest.fail("unsupported Public View must not reach the rendezvous service")

    monkeypatch.setenv("ANANTA_NETWORK_PROFILE", "public-ananta")
    monkeypatch.setattr(network_profile, "operator_tui_strict_pair_supported", lambda: False)
    monkeypatch.setattr(share_client, "update_session_permissions", unexpected_public_update)
    game: dict = {}

    share_action_set_view(
        NoHubTui(),
        game,
        "oidc-token",
        "hub-secret",
        "https://hub.invalid",
        "public-session-id",
        True,
    )

    assert game["share_status_message"].startswith("strict_pair_adapter_required:")


def test_share_menu_has_invite_button_when_sessions_exist():
    sessions = [{"id": "abc-123", "title": "Test", "participants": []}]
    lines = _plain_lines({"sessions": sessions, "sessions_mine": sessions, "sessions_joined": []})
    btn_lines = [line for line in lines if "[▶ :share invite]" in line]
    assert len(btn_lines) >= 1


def test_share_section_renders_active_session_from_live_state():
    from client_surfaces.operator_tui.models import OperatorMode, OperatorState
    from client_surfaces.operator_tui.renderer import _share_section_content_lines

    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="share",
        selected_index=0,
        mode=OperatorMode.NORMAL,
        header_logo_game={
            "share_active_session": {
                "id": "sess-live-1",
                "title": "Live Session",
                "invite_code": "LIVE123",
                "participants": [],
            }
        },
    )

    lines = _share_section_content_lines({"sessions": []}, state, 100)
    plain = "\n".join(_strip_ansi(line) for line in lines)

    assert "Sessions (1)" in plain
    assert "Live Session" in plain
    assert "Keine aktiven Sessions" not in plain


def test_share_menu_has_help_button():
    lines = _plain_lines({})
    btn_lines = [line for line in lines if "[▶ :share help]" in line]
    assert len(btn_lines) >= 1


def test_all_btn_lines_are_extractable():
    """Jede Zeile mit [▶ ...] muss extract_click_command zurückgeben."""
    import re

    ansi = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-9;?]*[ -/]*[@-~])")
    lines = build_share_section_lines({})
    for line in lines:
        plain = ansi.sub("", line)
        if "[▶ " in plain:
            cmd = extract_click_command(plain)
            assert cmd is not None and cmd.startswith(":"), f"Kein Befehl in Zeile: {plain!r}"


# ── Colon in Snake-Mode ───────────────────────────────────────────────────────


def test_colon_opens_command_mode_in_snake_mode():
    """`:` muss auch in Snake-Mode den Command-Mode öffnen."""
    from client_surfaces.operator_tui.commands import execute_command
    from client_surfaces.operator_tui.models import OperatorMode, OperatorState

    # Simulierter State: snake mode aktiv (ui_steering=True)
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="dashboard",
        selected_index=0,
        mode=OperatorMode.NORMAL,
        header_logo_game={"active": True, "ui_steering": True},
    )
    # :section share muss ausführbar sein — würde ohne Fix scheitern, da snake mode aktiv
    result = execute_command(":section share", state)
    assert result.state.section_id == "share"
    assert result.handled
