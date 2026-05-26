"""T07.04: E2E-Cast für Multi-Snake Chat und Notes.

Zeigt:
  - :chat ai + AI-Frage → Antwort im AI-Channel
  - :notes + private Notiz → bleibt lokal sichtbar
  - Zweite Teilnehmer-Snake im Room-Chat
  - Notes erscheinen NICHT im Raumchat

Output: assets/operator_tui_multisnake_chat.cast
Duration: ~60s | Headless, ohne manuelle Eingabe
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

_REPO = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO))

from client_surfaces.operator_tui.renderer import render_operator_shell
from client_surfaces.operator_tui.models import OperatorState
from client_surfaces.operator_tui.chat_state import (
    default_chat_state, make_message, append_message, switch_channel,
    ChannelType,
)
from client_surfaces.operator_tui.ai_snake_context import default_ai_context

WIDTH = 140
HEIGHT = 36
ASSETS = _REPO / "assets"
CAST_FILE = ASSETS / "operator_tui_multisnake_chat.cast"
CHAPTERS_FILE = ASSETS / "operator_tui_multisnake_chat.chapters.json"


def _base_game() -> dict:
    game: dict = {
        "active": True,
        "free_mode": True,
        "board_w": WIDTH - 44,
        "board_h": HEIGHT - 6,
        "snake": [[10, 5], [9, 5], [8, 5], [7, 5], [6, 5]],
        "food": (20, 10),
        "direction": (1, 0),
        "score": 0,
        "speed_level": 3,
        "tps_override": 12,
        "local_snake_id": "s-op",
        "snake_color": "mint",
        "tutor_depth_mode": "overview",
        "snakes": {
            "s-op": {
                "id": "s-op", "pseudonym": "operator", "role": "player",
                "snake_color": "mint", "active": True, "local": True,
                "snake": [[10, 5], [9, 5], [8, 5]],
            },
            "s-ai": {
                "id": "s-ai", "pseudonym": "tutor-ai", "role": "tutor",
                "snake_color": "amber", "active": True,
                "snake": [[30, 8], [29, 8], [28, 8]],
                "message": "Hallo! Ich bin die tutor-ai snake.",
            },
        },
        "paused": False,
        "tutorial_mode": False,
        "_scores_cache": {"high": 42, "last": 15, "games": 7},
    }
    chat = default_chat_state("s-op")
    game["chat_state"] = chat
    game["ai_snake_context"] = default_ai_context()
    return game


def _render_frame(game: dict, command_line: str = "") -> str:
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="dashboard",
        status_message=command_line,
        header_logo_game=game,
        command_line=command_line,
    )
    return render_operator_shell(state, width=WIDTH, height=HEIGHT)


def _ansi_frame(game: dict, ts: float, command_line: str = "") -> list:
    frame = _render_frame(game, command_line)
    # Clear screen + render
    output = "\x1b[2J\x1b[H" + frame
    return [ts, "o", output]


def _add_room_message(game: dict, sender_id: str, sender_color: str, text: str) -> None:
    chat = game["chat_state"]
    msg = make_message(
        channel_id="room:main", channel_type="room",
        sender_id=sender_id, sender_kind="user", text=text,
        delivery_state="received",
    )
    append_message(chat, msg)


def _add_ai_message(game: dict, sender_id: str, sender_kind: str, text: str) -> None:
    chat = game["chat_state"]
    msg = make_message(
        channel_id="ai:tutor", channel_type="ai",
        sender_id=sender_id, sender_kind=sender_kind, text=text,
        delivery_state="received" if sender_kind == "ai" else "sent",
        visibility="ai_context",
    )
    append_message(chat, msg)


def _add_note(game: dict, text: str) -> None:
    chat = game["chat_state"]
    msg = make_message(
        channel_id="notes:self", channel_type="notes",
        sender_id="s-op", sender_kind="user", text=text,
        delivery_state="sent", visibility="local_only",
    )
    append_message(chat, msg)


def _add_system_msg(game: dict, channel_id: str, text: str) -> None:
    chat = game["chat_state"]
    msg = make_message(
        channel_id=channel_id, channel_type="room" if channel_id == "room:main" else "system",
        sender_id="system", sender_kind="system", text=text,
        delivery_state="received", visibility="system",
    )
    append_message(chat, msg)


def _move_snake(game: dict, steps: int = 3) -> None:
    dx, dy = game.get("direction", (1, 0))
    snake = list(game["snake"])
    for _ in range(steps):
        hx, hy = snake[0]
        new_head = (hx + dx) % game["board_w"], (hy + dy) % game["board_h"]
        snake = [new_head] + snake[:-1]
    game["snake"] = snake
    # also update in snakes dict
    snakes = dict(game.get("snakes") or {})
    if "s-op" in snakes:
        s = dict(snakes["s-op"])
        s["snake"] = snake[:3]
        snakes["s-op"] = s
    game["snakes"] = snakes


def generate_cast() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    game = _base_game()
    events: list[list] = []
    chapters: list[dict] = []
    ts = 0.0

    def hold(seconds: float, command: str = "") -> None:
        """Keyframe: emit ONE frame, advance ts by `seconds`."""
        nonlocal ts
        _move_snake(game, steps=max(1, int(seconds * 2)))
        events.append(_ansi_frame(game, ts, command))
        ts += seconds

    # ── Chapter 1: Intro ─────────────────────────────────────────────────────
    chapters.append({"ts": 0, "title": "Intro – Snake-Modus mit Chat"})
    hold(5)
    hold(5)

    # ── Chapter 2: Zweite Snake tritt bei ─────────────────────────────────────
    chapters.append({"ts": round(ts, 2), "title": "Zweite Snake tritt dem Room bei"})
    _add_system_msg(game, "room:main", "* [system] s-peer joined the room")
    snakes = dict(game.get("snakes") or {})
    snakes["s-peer"] = {
        "id": "s-peer", "pseudonym": "peer-dev", "role": "player",
        "snake_color": "sky", "active": True,
        "snake": [[50, 15], [49, 15], [48, 15]],
    }
    game["snakes"] = snakes
    switch_channel(game["chat_state"], "room:main")
    hold(3)

    # Peer sendet room message
    _add_room_message(game, "s-peer", "sky", "hey! gerade in der goals section")
    hold(3)
    _add_room_message(game, "s-op", "mint", "cool – ich schaue mir gerade das dashboard an")
    hold(3)

    # ── Chapter 3: Raumchat wird sichtbar ─────────────────────────────────────
    chapters.append({"ts": round(ts, 2), "title": "Room-Chat aktiv"})
    hold(4)
    hold(4)

    # ── Chapter 4: :chat ai → AI-Frage ───────────────────────────────────────
    chapters.append({"ts": round(ts, 2), "title": ":chat ai – Frage an AI-Snake"})
    switch_channel(game["chat_state"], "ai:tutor")
    game["chat_state"]["chat_focus"] = True
    game["chat_state"]["chat_input_buffer"] = ""

    # Simulate typing (keyframes at word boundaries)
    q = "was macht der Hub in Ananta?"
    words = q.split()
    buf = ""
    for word in words:
        buf = (buf + " " + word).strip()
        game["chat_state"]["chat_input_buffer"] = buf
        events.append(_ansi_frame(game, ts, f"@ai> {buf}"))
        ts += 0.6
    hold(1)

    # Send question
    game["chat_state"]["chat_input_buffer"] = ""
    game["chat_state"]["chat_focus"] = False
    _add_ai_message(game, "s-op", "user", q)
    game["chat_state"]["ai_typing"] = True
    hold(3)

    # AI antwortet
    ai_answer = "Der Hub ist der Control Plane von Ananta – er orchestriert Worker, verwaltet Tasks und hält den Systemzustand."
    game["chat_state"]["ai_typing"] = False
    _add_ai_message(game, "s-ai", "ai", ai_answer)
    hold(5)
    hold(4)

    # ── Chapter 5: :notes – private Notiz ────────────────────────────────────
    chapters.append({"ts": round(ts, 2), "title": ":notes – privater Notizblock"})
    switch_channel(game["chat_state"], "notes:self")
    game["chat_state"]["chat_focus"] = True
    game["chat_state"]["chat_input_buffer"] = ""
    hold(2)

    note_text = "TODO: Hub-Architektur weiter vertiefen"
    # Typing keyframes at word boundaries
    words_n = note_text.split()
    buf_n = ""
    for word in words_n:
        buf_n = (buf_n + " " + word).strip()
        game["chat_state"]["chat_input_buffer"] = buf_n
        events.append(_ansi_frame(game, ts, f"notes> {buf_n}"))
        ts += 0.5

    # Save note (local only)
    game["chat_state"]["chat_input_buffer"] = ""
    game["chat_state"]["chat_focus"] = False
    _add_note(game, note_text)
    hold(4)

    # ── Chapter 6: Notes erscheinen NICHT in room-chat ────────────────────────
    chapters.append({"ts": round(ts, 2), "title": "Notes bleiben local-only"})
    switch_channel(game["chat_state"], "room:main")
    hold(4)
    # Room chat still only shows room messages – NOT the note
    _add_room_message(game, "s-peer", "sky", "alles klar hier!")
    hold(4)
    hold(3)

    # ── Outro ─────────────────────────────────────────────────────────────────
    chapters.append({"ts": round(ts, 2), "title": "Outro"})
    hold(4)
    hold(4)

    total_duration = round(ts, 2)

    # ── Write asciinema v2 cast ───────────────────────────────────────────────
    header = {
        "version": 2,
        "width": WIDTH,
        "height": HEIGHT,
        "timestamp": int(time.time()),
        "title": "Ananta TUI – Multi-Snake Chat + Notes",
        "env": {"TERM": "xterm-256color"},
    }
    with CAST_FILE.open("w", encoding="utf-8") as f:
        f.write(json.dumps(header) + "\n")
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    # ── Write chapters JSON ───────────────────────────────────────────────────
    CHAPTERS_FILE.write_text(json.dumps(chapters, indent=2), encoding="utf-8")

    size_kb = CAST_FILE.stat().st_size // 1024
    print(f"Cast: {CAST_FILE.name}  {total_duration:.1f}s  {size_kb}KB  {len(events)} frames")
    print(f"Chapters: {CHAPTERS_FILE.name}  {len(chapters)} chapters")

    # Acceptance checks
    assert total_duration >= 45, f"Cast too short: {total_duration}s"
    assert total_duration <= 90, f"Cast too long: {total_duration}s"
    assert size_kb <= 400, f"Cast too large: {size_kb}KB"


if __name__ == "__main__":
    generate_cast()
