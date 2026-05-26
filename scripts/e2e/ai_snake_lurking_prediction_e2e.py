"""E08.05: E2E-Cast für AI-Snake Lurk/Follow/Prediction.

Output:
  - assets/operator_tui_ai_snake_lurking_prediction.cast
  - assets/operator_tui_ai_snake_lurking_prediction.chapters.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO))

from client_surfaces.operator_tui.ai_snake_context import default_ai_context
from client_surfaces.operator_tui.chat_state import default_chat_state
from client_surfaces.operator_tui.models import OperatorState
from client_surfaces.operator_tui.renderer import render_operator_shell

WIDTH = 140
HEIGHT = 36
ASSETS = _REPO / "assets"
CAST_FILE = ASSETS / "operator_tui_ai_snake_lurking_prediction.cast"
CHAPTERS_FILE = ASSETS / "operator_tui_ai_snake_lurking_prediction.chapters.json"


def _frame(game: dict, ts: float, status: str = "") -> list[object]:
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id=str(game.get("_section") or "dashboard"),
        header_logo_game=game,
        status_message=status,
        command_line="",
    )
    return [float(ts), "o", "\x1b[2J\x1b[H" + render_operator_shell(state, width=WIDTH, height=HEIGHT)]


def _base_game() -> dict:
    game: dict = {
        "active": True,
        "free_mode": True,
        "board_w": WIDTH - 44,
        "board_h": HEIGHT - 6,
        "snake": [[10, 8], [9, 8], [8, 8], [7, 8]],
        "direction": (1, 0),
        "score": 0,
        "speed_level": 3,
        "local_snake_id": "s-op",
        "ai_snake_mode": "lurking_follow",
        "ai_snake_runtime_status": "lurking",
        "ai_snake_prediction": {"predicted_intent": "section_scan", "target_ref": "section:dashboard", "confidence": 0.42},
        "ai_snake_debug": {"gate_reason": "confidence_low"},
        "ai_snake_context_envelope": {"context_hash": "ctx-operator-tui-snake-v1"},
        "snakes": {
            "s-op": {"id": "s-op", "pseudonym": "operator", "snake_color": "mint", "active": True, "snake": [[10, 8], [9, 8], [8, 8]]},
            "s-ai": {"id": "s-ai", "pseudonym": "lurking-ai", "snake_color": "amber", "active": True, "snake": [[25, 10], [24, 10], [23, 10]]},
        },
        "chat_state": default_chat_state("s-op"),
        "ai_snake_context": default_ai_context(),
        "_section": "dashboard",
    }
    return game


def _move(game: dict, steps: int = 2) -> None:
    snake = list(game.get("snake") or [])
    if not snake:
        return
    board_w = max(1, int(game.get("board_w") or 60))
    board_h = max(1, int(game.get("board_h") or 20))
    dx, dy = game.get("direction", (1, 0))
    for _ in range(max(1, int(steps))):
        hx, hy = snake[0]
        head = ((hx + int(dx)) % board_w, (hy + int(dy)) % board_h)
        snake = [list(head)] + snake[:-1]
    game["snake"] = snake
    snakes = dict(game.get("snakes") or {})
    op = dict(snakes.get("s-op") or {})
    op["snake"] = snake[:3]
    snakes["s-op"] = op
    ai = dict(snakes.get("s-ai") or {})
    ai_snake = list(ai.get("snake") or [[25, 10], [24, 10], [23, 10]])
    ahx, ahy = ai_snake[0]
    target = snake[0]
    tx = ahx + (1 if target[0] > ahx else (-1 if target[0] < ahx else 0))
    ty = ahy + (1 if target[1] > ahy else (-1 if target[1] < ahy else 0))
    ai_snake = [[tx % board_w, ty % board_h]] + ai_snake[:-1]
    ai["snake"] = ai_snake
    snakes["s-ai"] = ai
    game["snakes"] = snakes


def generate_cast() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    game = _base_game()
    chapters: list[dict[str, object]] = []
    events: list[list[object]] = []
    ts = 0.0

    def hold(seconds: float, *, status: str = "", frames: int = 2) -> None:
        nonlocal ts
        frames = max(1, int(frames))
        for _ in range(frames):
            _move(game, steps=1)
            events.append(_frame(game, ts, status=status))
            ts += seconds / frames

    chapters.append({"ts": 0.0, "title": "Intro: Lurking mode aktiv"})
    hold(12.0, status="ai:lurking_follow/lurking pred=section_scan", frames=12)

    chapters.append({"ts": round(ts, 2), "title": "Follow: User bewegt sich zu Artifacts"})
    game["_section"] = "artifacts"
    game["direction"] = (1, 1)
    game["ai_snake_runtime_status"] = "following"
    game["ai_snake_prediction"] = {"predicted_intent": "artifact_explain", "target_ref": "client_surfaces/operator_tui/renderer.py", "confidence": 0.78}
    game["ai_snake_debug"] = {"gate_reason": "stable_prediction"}
    hold(20.0, status="ai:follows target=renderer.py", frames=20)

    chapters.append({"ts": round(ts, 2), "title": "Prediction-Kommentar + ContextEnvelope"})
    game["chat_state"]["active_channel"] = "ai:tutor"
    game["chat_state"]["channels"]["ai:tutor"]["messages"].append(
        {
            "message_id": "m-proactive",
            "channel_id": "ai:tutor",
            "channel_type": "ai",
            "sender_id": "s-ai",
            "sender_kind": "ai",
            "text": "Ich glaube, du willst renderer.py verstehen (artifact_explain, conf=0.78).",
            "timestamp": 1.0,
            "delivery_state": "received",
            "visibility": "ai_context",
            "meta": {},
        }
    )
    hold(18.0, status="proactive-comment + ctx hash", frames=18)

    chapters.append({"ts": round(ts, 2), "title": "Quiet mode und manuelles Explain"})
    game["ai_snake_mode"] = "quiet"
    game["ai_snake_runtime_status"] = "quiet"
    game["ai_snake_prediction"] = {"predicted_intent": "chat", "target_ref": "ai:tutor", "confidence": 0.61}
    game["ai_snake_debug"] = {"gate_reason": "quiet_mode"}
    hold(10.0, status="ai:quiet no proactive", frames=10)
    game["ai_snake_mode"] = "point_to_target"
    game["ai_snake_runtime_status"] = "pointing"
    hold(8.0, status=":ai explain -> forced question", frames=8)

    cast_header = {
        "version": 2,
        "width": WIDTH,
        "height": HEIGHT,
        "title": "Ananta Operator TUI – AI Snake Lurking Prediction Demo",
        "env": {"TERM": "xterm-256color", "COLORTERM": "truecolor"},
    }
    lines = [json.dumps(cast_header, ensure_ascii=False)]
    lines.extend(json.dumps(event, ensure_ascii=False) for event in events)
    CAST_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    CHAPTERS_FILE.write_text(json.dumps(chapters, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"cast: {CAST_FILE}")
    print(f"chapters: {CHAPTERS_FILE}")
    print(f"duration: {round(ts, 2)}s")


if __name__ == "__main__":
    generate_cast()
