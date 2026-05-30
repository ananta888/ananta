"""Tests: :- Kommandos in Snake-Mode und Share-Section Maus-Bedienung."""
from __future__ import annotations

import pytest

from client_surfaces.operator_tui.share_menu import (
    build_share_section_lines,
    extract_click_command,
)


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
    assert extract_click_command(line, x=line.index(":share view off")) == ":share view off"
    assert extract_click_command(line, x=0) is None


# ── share_menu enthält klickbare Buttons ──────────────────────────────────────

def _plain_lines(payload=None, **kw) -> list[str]:
    import re
    ansi = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-9;?]*[ -/]*[@-~])")
    lines = build_share_section_lines(payload or {}, **kw)
    return [ansi.sub("", l) for l in lines]


def test_share_menu_has_oidc_login_button_when_not_logged_in():
    lines = _plain_lines({"oidc_status": {}})
    btn_lines = [l for l in lines if "[▶ :oidc login]" in l]
    assert len(btn_lines) >= 1


def test_share_menu_has_key_generate_button_when_no_key(tmp_path):
    from unittest.mock import patch
    from client_surfaces.operator_tui.device_keys import DeviceKeyManager
    mgr = DeviceKeyManager(tmp_path / "nokeys")
    with patch("client_surfaces.operator_tui.share_menu.get_device_key_manager", return_value=mgr):
        lines = _plain_lines({})
    btn_lines = [l for l in lines if "[▶ :share key generate]" in l]
    assert len(btn_lines) >= 1


def test_share_menu_has_create_button_when_no_sessions():
    lines = _plain_lines({"sessions": []})
    btn_lines = [l for l in lines if "[▶ :share create]" in l]
    assert len(btn_lines) >= 1


def test_share_menu_has_join_button_when_no_sessions():
    lines = _plain_lines({"sessions": []})
    btn_lines = [l for l in lines if "[▶ :share join]" in l]
    assert len(btn_lines) >= 1


def test_share_menu_has_invite_button_when_sessions_exist():
    sessions = [{"id": "abc-123", "title": "Test", "participants": []}]
    lines = _plain_lines({"sessions": sessions})
    btn_lines = [l for l in lines if "[▶ :share invite]" in l]
    assert len(btn_lines) >= 1


def test_share_menu_has_help_button():
    lines = _plain_lines({})
    btn_lines = [l for l in lines if "[▶ :share help]" in l]
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
    from client_surfaces.operator_tui.models import OperatorState, OperatorMode
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
