from __future__ import annotations

import re
import time

from client_surfaces.operator_tui.models import OperatorState
from client_surfaces.operator_tui.renderer import (
    _overlay_fullscreen_snake,
    _overlay_snake_chat_panel,
    _snake_right_panel_width,
)


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _strip_ansi(line: str) -> str:
    return _ANSI_RE.sub("", line)


def test_split_panel_width_is_wider_than_legacy_default() -> None:
    assert _snake_right_panel_width(120) >= 40


def test_fullscreen_overlay_keeps_snake_out_of_right_panel_area() -> None:
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
    assert row[118] not in {"●", "◉", "·"}
    assert any(ch in row for ch in ("●", "◉", "·"))


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
