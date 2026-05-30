from __future__ import annotations

import re
import time

from client_surfaces.operator_tui.models import OperatorState
from client_surfaces.operator_tui.renderer import (
    _overlay_snake_ai_panel,
    _overlay_fullscreen_snake,
    _overlay_snake_chat_panel,
    _snake_right_panel_width,
    _visible_char_at,
)


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _strip_ansi(line: str) -> str:
    return _ANSI_RE.sub("", line)


def test_split_panel_width_is_wider_than_legacy_default() -> None:
    assert _snake_right_panel_width(120) >= 40


def test_fullscreen_overlay_allows_snake_in_right_area() -> None:
    lines = [" " * 120 for _ in range(24)]
    game = {
        "active": True,
        "free_mode": True,
        "snake": [[118, 10], [117, 10]],
        "local_snake_id": "s1",
        "snakes": {
            "s1": {"snake": [[118, 10], [117, 10]], "snake_color": "mint"},
        },
    }
    state = OperatorState(endpoint="http://localhost", header_logo_game=game)
    out = _overlay_fullscreen_snake(lines, state, width=120, body_start=0, body_end=24)
    row = _strip_ansi(out[10])
    assert row[118] in {"●", "◉", "·"}


def test_chat_panel_renders_timestamps_and_ai_snake_sender() -> None:
    lines = [" " * 120 for _ in range(30)]
    created_at = time.time()
    game = {
        "chat_state": {
            "active_channel": "ai:tutor",
            "chat_focus": False,
            "channels": {
                "ai:tutor": {
                    "channel_id": "ai:tutor",
                    "channel_type": "ai",
                    "display_name": "AI tutor-ai",
                    "messages": [
                        {
                            "sender_id": "s-ai",
                            "sender_kind": "ai",
                            "text": "Hallo aus der AI-Snake",
                            "created_at": created_at,
                            "delivery_state": "received",
                        }
                    ],
                    "unread": 0,
                },
                "room:main": {"display_name": "#room", "messages": [], "unread": 0},
                "notes:self": {"display_name": "notes local-only", "messages": [], "unread": 0},
            },
        }
    }
    out = _overlay_snake_chat_panel(lines, game, split_col=78, panel_width=40, ai_rows=12, height=30)
    rendered = "\n".join(_strip_ansi(line) for line in out)
    expected_ts = time.strftime("%H:%M", time.localtime(created_at))
    assert expected_ts in rendered
    assert "AI-snake" in rendered
    assert "Hallo aus der" in rendered
    assert "AI-Snake" in rendered


def test_split_panel_no_longer_blanks_right_column_in_snake_overlay() -> None:
    width = 120
    lines = ["X" * width for _ in range(24)]
    game = {
        "active": True,
        "free_mode": True,
        "snake": [[10, 10], [9, 10]],
        "local_snake_id": "s1",
        "snakes": {"s1": {"snake": [[10, 10], [9, 10]], "snake_color": "mint"}},
        "chat_state": {
            "active_channel": "room:main",
            "chat_focus": False,
            "channels": {
                "room:main": {"display_name": "#room", "messages": [], "unread": 0},
                "ai:tutor": {"display_name": "AI tutor-ai", "messages": [], "unread": 0},
                "notes:self": {"display_name": "notes local-only", "messages": [], "unread": 0},
            },
        },
    }
    state = OperatorState(endpoint="http://localhost", header_logo_game=game)
    out = _overlay_fullscreen_snake(lines, state, width=width, body_start=0, body_end=24)
    for row in out:
        assert _visible_char_at(row, width - 2) == "X"


def test_ai_panel_shows_toggle_configuration_and_keys() -> None:
    lines = [" " * 120 for _ in range(20)]
    game = {
        "tutorial_mode": True,
        "chat_panel_open": False,
        "score": 3,
        "speed_level": 2,
        "llm_status": {"reachable": False},
        "ai_snake_mode": "lurking_follow",
        "ai_snake_runtime_status": "running",
        "ai_snake_monitor_log": [{"event": "tutorial_toggled", "label": "Tutorial-AI umgeschaltet", "created_at": 1735682400.0}],
    }
    out = _overlay_snake_ai_panel(lines, game, split_col=78, panel_width=40, height=16, row_start=0, chat_enabled=False)
    rendered = "\n".join(_strip_ansi(line) for line in out)
    assert "Auto-Heuristik [Ctrl+U]: AN" in rendered
    assert "AI-Chat [Ctrl+G]: AUS" in rendered
    assert "Chat-Fokus [Ctrl+E]" in rendered
    assert "Steuerung: mode=lurking_follow runtime=running" in rendered
    assert "AI-Snake Verlauf:" in rendered


def test_chat_panel_shows_disabled_state_when_chat_is_off() -> None:
    lines = [" " * 120 for _ in range(24)]
    game = {
        "chat_state": {
            "active_channel": "ai:tutor",
            "chat_focus": False,
            "channels": {
                "room:main": {"display_name": "#room", "messages": [], "unread": 0},
                "ai:tutor": {"display_name": "AI tutor-ai", "messages": [], "unread": 0},
                "notes:self": {"display_name": "notes local-only", "messages": [], "unread": 0},
            },
        }
    }
    out = _overlay_snake_chat_panel(lines, game, split_col=78, panel_width=40, ai_rows=8, height=24, enabled=False)
    rendered = "\n".join(_strip_ansi(line) for line in out)
    assert "AI-Chat ist deaktiviert." in rendered
    assert "Mit Ctrl+G wieder aktivieren." in rendered
