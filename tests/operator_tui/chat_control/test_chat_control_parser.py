from __future__ import annotations

import pytest

from client_surfaces.operator_tui.chat_control_parser import (
    ParsedCommand,
    is_chat_command,
    parse_chat_command,
)


def test_not_a_command_returns_error():
    r = parse_chat_command("hello world")
    assert not r.ok
    assert r.action_id == ""


def test_double_slash_not_a_chat_command():
    assert not is_chat_command("//")
    assert not is_chat_command("//shortcuts")


def test_is_chat_command():
    assert is_chat_command("/view list")
    assert is_chat_command("/help tui")


def test_view_list():
    r = parse_chat_command("/view list")
    assert r.ok and r.action_id == "view.list"


def test_view_next():
    r = parse_chat_command("/view next")
    assert r.ok and r.action_id == "view.next"


def test_view_previous():
    r = parse_chat_command("/view previous")
    assert r.ok and r.action_id == "view.previous"


def test_view_prev_alias():
    r = parse_chat_command("/view prev")
    assert r.ok and r.action_id == "view.previous"


def test_view_select_by_name():
    r = parse_chat_command("/view snake")
    assert r.ok and r.action_id == "view.select"
    assert r.args[0] == "snake_debug"


def test_view_select_markdown_alias():
    r = parse_chat_command("/view markdown")
    assert r.ok and r.action_id == "view.select"
    assert r.args[0] == "markdown_mermaid_document"


def test_view_select_diagnostics_alias():
    r = parse_chat_command("/view diagnostics")
    assert r.ok and r.action_id == "view.select"
    assert r.args[0] == "renderer_diagnostics"


def test_overlay_views_on():
    r = parse_chat_command("/overlay views on")
    assert r.ok and r.action_id == "overlay.views.on"


def test_overlay_views_off():
    r = parse_chat_command("/overlay views off")
    assert r.ok and r.action_id == "overlay.views.off"


def test_overlay_views_toggle_explicit():
    r = parse_chat_command("/overlay views toggle")
    assert r.ok and r.action_id == "overlay.views.toggle"


def test_overlay_views_default_toggle():
    r = parse_chat_command("/overlay views")
    assert r.ok and r.action_id == "overlay.views.toggle"


def test_focus_chat():
    r = parse_chat_command("/focus chat")
    assert r.ok and r.action_id == "focus.chat"


def test_focus_artifacts():
    r = parse_chat_command("/focus artifacts")
    assert r.ok and r.action_id == "focus.artifacts"


def test_open_artifact():
    r = parse_chat_command("/open artifact 3")
    assert r.ok and r.action_id == "artifact.open"
    assert r.args[0] == "3"


def test_open_artifact_missing_ref():
    r = parse_chat_command("/open artifact")
    assert not r.ok


def test_snake_pause():
    r = parse_chat_command("/snake pause")
    assert r.ok and r.action_id == "snake.pause"


def test_snake_resume():
    r = parse_chat_command("/snake resume")
    assert r.ok and r.action_id == "snake.resume"


def test_snake_follow_on():
    r = parse_chat_command("/snake follow on")
    assert r.ok and r.action_id == "snake.follow.on"


def test_snake_follow_off():
    r = parse_chat_command("/snake follow off")
    assert r.ok and r.action_id == "snake.follow.off"


def test_help_tui():
    r = parse_chat_command("/help tui")
    assert r.ok and r.action_id == "help.tui"


def test_help_no_arg():
    r = parse_chat_command("/help")
    assert r.ok and r.action_id == "help.tui"


def test_invalid_command_returns_error():
    r = parse_chat_command("/rm -rf /")
    assert not r.ok
    assert "unknown command" in r.error.lower()


def test_invalid_overlay_modifier():
    r = parse_chat_command("/overlay views sideways")
    assert not r.ok


def test_view_no_subcommand():
    r = parse_chat_command("/view")
    assert not r.ok


def test_quoted_argument():
    r = parse_chat_command('/open artifact "my artifact 3"')
    assert r.ok and r.args[0] == "my artifact 3"


def test_parser_has_no_side_effects():
    for cmd in ["/view list", "/snake pause", "/focus chat"]:
        r1 = parse_chat_command(cmd)
        r2 = parse_chat_command(cmd)
        assert r1 == r2


def test_nl_mode_maps_phrase():
    r = parse_chat_command("nächste view", nl_mode_enabled=True)
    assert r.ok and r.action_id == "view.next"


def test_nl_mode_ambiguous_phrase_returns_error():
    r = parse_chat_command("irgendwas unbekanntes", nl_mode_enabled=True)
    assert not r.ok


def test_nl_mode_disabled_by_default():
    r = parse_chat_command("nächste view")
    assert not r.ok
