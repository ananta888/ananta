#!/usr/bin/env python3
"""T05.01 / T05.03 / T05.04: Cast-Erzeugung für den Erklär-AI-Snake Splash.

Erzeugt assets/operator_tui_splash.cast (55–65 Sekunden, max. 300 KB, 120×32).
Zeigt: Intro → Dashboard → Goals → Snake-Modus → :ask-Interaktion → Outro.

Aufruf:
    python scripts/e2e/snake_splash_e2e.py [--out assets/operator_tui_splash.cast]

Oder:
    make cast
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from client_surfaces.operator_tui.models import FocusPane, OperatorState, PanelState
from client_surfaces.operator_tui.renderer import render_operator_shell

WIDTH = 120
HEIGHT = 32
CHAPTERS: list[dict] = []  # wird während Aufbau befüllt


def _asciinema_v2(*, title: str, frames: list[tuple[float, str]]) -> str:
    header = {
        "version": 2,
        "width": WIDTH,
        "height": HEIGHT,
        "title": title,
        "env": {"TERM": "xterm-256color", "COLORTERM": "truecolor"},
    }
    lines = [json.dumps(header, ensure_ascii=False)]
    for ts, frame in frames:
        lines.append(json.dumps([float(ts), "o", frame], ensure_ascii=False))
    return "\n".join(lines) + "\n"


def _render(state: OperatorState) -> str:
    return "\x1b[2J\x1b[H" + render_operator_shell(state, width=WIDTH, height=HEIGHT) + "\n"


def _base_state(**kw) -> OperatorState:
    payloads = {
        "dashboard": {
            "agents": {"online": 3, "total": 3},
            "queue": {"depth": 2},
            "task_summary": "2 running tasks · 1 active goal",
        },
        "goals": {
            "items": [
                {"id": "g-tui", "title": "TUI-Onboarding verbessern", "status": "active"},
                {"id": "g-snake", "title": "AI-Snake Erklärungen ausbauen", "status": "ready"},
            ]
        },
        "tasks": {
            "running": 2,
            "queued": 3,
            "summary": "worker-1: g-tui · worker-2: cast-validation",
        },
    }
    return OperatorState(
        endpoint="http://localhost:5000",
        panel_states={"dashboard": PanelState.HEALTHY, "goals": PanelState.HEALTHY, "tasks": PanelState.HEALTHY},
        section_payloads=payloads,
        **kw,
    )


def _snake_game(
    *,
    snake: list[tuple[int, int]],
    ai_snake: list[tuple[int, int]],
    ai_message: str = "",
    ai_question: str = "",
    ai_answer: str = "",
    ai_answered: bool = False,
    tutor_pointer: dict | None = None,
    score: int = 0,
    paused: bool = False,
    speed_level: int = 3,
) -> dict:
    history = []
    if ai_message:
        history.append({"at": time.monotonic(), "source": "event", "target": "content", "text": ai_message})
    snakes = {
        "s1": {
            "id": "s1",
            "pseudonym": "local-snake",
            "snake": snake,
            "trail_path": list(snake),
            "snake_color": "mint",
            "active": True,
            "local": True,
            "role": "player",
        },
        "s-ai": {
            "id": "s-ai",
            "pseudonym": "tutor-ai",
            "snake": ai_snake,
            "trail_path": list(ai_snake),
            "snake_color": "amber",
            "active": True,
            "local": False,
            "role": "tutor",
            "message": ai_message,
            "message_style": "ticker",
        },
    }
    game: dict = {
        "active": True,
        "alive": True,
        "free_mode": True,
        "paused": paused,
        "snake": snake,
        "trail_path": list(snake),
        "snake_color": "mint",
        "local_snake_id": "s1",
        "snakes": snakes,
        "board_w": WIDTH,
        "board_h": HEIGHT,
        "score": score,
        "moves": score * 20,
        "speed_level": speed_level,
        "tutor_depth_mode": "overview",
        "tutorial_mode": True,
        "tutorial_propose_history": history,
        "tutor_ask_question": ai_question,
        "tutor_ask_answer": ai_answer,
        "tutor_ask_answered": ai_answered,
    }
    if tutor_pointer:
        game["tutor_pointer"] = tutor_pointer
    return game


def build_frames() -> list[tuple[float, str]]:
    """Erzeugt ~30 Keyframes mit je 1.5–2.5s Hold → Gesamt ca. 55–60s, < 280 KB."""
    frames: list[tuple[float, str]] = []
    t = 0.0

    def push(state: OperatorState, hold: float) -> None:
        nonlocal t
        frames.append((t, _render(state)))
        t += hold

    # ── Chapter 1: Intro (0–10s, 5 Frames) ──────────────────────────────────
    CHAPTERS.append({"t": 0.0, "title": "Intro", "description": "Ananta Operator TUI – Snake-Modus startet"})
    for step in range(5):
        sx = 20 + step * 8
        push(_base_state(
            focus=FocusPane.HEADER, section_id="dashboard", selected_index=0,
            status_message="snake:fullscreen speed:3/5 · Ananta Operator TUI",
            header_logo_game=_snake_game(
                snake=[(sx - i * 2, 16) for i in range(8)],
                ai_snake=[(sx - i * 2 + 5, 14) for i in range(6)],
                ai_message="Willkommen! Ich bin die tutor-ai-Schlange. Ich erkläre die Ananta-Architektur.",
            ),
        ), hold=2.0)

    # ── Chapter 2: Dashboard (10–22s, 6 Frames) ──────────────────────────────
    CHAPTERS.append({"t": 10.0, "title": "Dashboard", "description": "KI erklärt das Dashboard und Zeige-Funktion"})
    t = 10.0
    for step in range(4):
        ptr = {"target": "dashboard", "expires": t + 10, "blink_frame": step * 2}
        push(_base_state(
            focus=FocusPane.NAVIGATION, section_id="dashboard", selected_index=0,
            status_message="snake:fullscreen · Dashboard-Erklärung",
            header_logo_game=_snake_game(
                snake=[(50 - step * 4 - i * 2, 20) for i in range(8)],
                ai_snake=[(55 - step * 4 - i * 2, 18) for i in range(6)],
                ai_message="Dashboard: Agenten-Status, Queue-Tiefe und Goals. Wie ein Kontrollzentrum.",
                tutor_pointer=ptr, score=2,
            ),
        ), hold=2.0)
    # Zeige-Funktion #2: Goals-Pointer
    for step in range(2):
        ptr2 = {"target": "goals", "expires": t + 10, "blink_frame": step * 2}
        push(_base_state(
            focus=FocusPane.NAVIGATION, section_id="dashboard", selected_index=1,
            status_message="snake:fullscreen · Goals-Zeige",
            header_logo_game=_snake_game(
                snake=[(40 - step * 4 - i * 2, 22) for i in range(8)],
                ai_snake=[(45 - step * 4 - i * 2, 20) for i in range(6)],
                ai_message="Schau auf Goals – dort definierst du Ziele für Ananta.",
                tutor_pointer=ptr2, score=3,
            ),
        ), hold=2.0)

    # ── Chapter 3: Goals (22–34s, 4 Frames) ──────────────────────────────────
    CHAPTERS.append({"t": 22.0, "title": "Goals", "description": "KI erklärt Goals und beantwortet Frage"})
    t = 22.0
    for step in range(2):
        push(_base_state(
            focus=FocusPane.CONTENT, section_id="goals", selected_index=1,
            status_message="snake:fullscreen · Goals-Sektion",
            header_logo_game=_snake_game(
                snake=[(45 - step * 4 - i * 2, 15) for i in range(8)],
                ai_snake=[(50 - step * 4 - i * 2, 13) for i in range(6)],
                ai_message="Goals: Jedes Ziel wird in Tasks aufgeteilt. Score = dein Fortschritt.",
                score=5,
            ),
        ), hold=2.5)

    # :ask-Interaktion (T02.03) – 2 Frames pausiert, 2 Frames mit Antwort
    CHAPTERS.append({"t": 27.0, "title": ":ask Demo", "description": "Operator fragt die AI-Schlange direkt"})
    t = 27.0
    for step in range(2):
        push(_base_state(
            focus=FocusPane.CONTENT, section_id="goals", selected_index=1,
            status_message=":ask Was ist ein Context Bundle?",
            header_logo_game=_snake_game(
                snake=[(40 - step * 2 - i * 2, 14) for i in range(8)],
                ai_snake=[(45 - step * 2 - i * 2, 12) for i in range(6)],
                ai_question="Was ist ein Context Bundle?",
                ai_answered=False, score=5, paused=True,
            ),
        ), hold=1.5)
    for step in range(2):
        push(_base_state(
            focus=FocusPane.CONTENT, section_id="goals", selected_index=1,
            status_message=":ask – Antwort erhalten, Resume",
            header_logo_game=_snake_game(
                snake=[(38 - step * 2 - i * 2, 13) for i in range(8)],
                ai_snake=[(43 - step * 2 - i * 2, 11) for i in range(6)],
                ai_question="Was ist ein Context Bundle?",
                ai_answer="Ein Context Bundle fasst Dateien, Git-History und Artefakte für den LLM-Kontext zusammen.",
                ai_answered=True, score=6,
            ),
        ), hold=2.0)

    # ── Chapter 4: Snake-Modus (34–50s, 8 Frames) ────────────────────────────
    CHAPTERS.append({"t": 34.0, "title": "Snake-Modus", "description": "Spielfeld, Multi-Snake und Erklärungen"})
    t = 34.0
    for step in range(8):
        push(_base_state(
            focus=FocusPane.HEADER, section_id="tasks", selected_index=2,
            status_message=f"snake:fullscreen speed:3/5 · score:{6 + step}",
            header_logo_game=_snake_game(
                snake=[(35 - step * 3 - i * 2, 16) for i in range(8)],
                ai_snake=[(45 - step * 3 - i * 2, 12) for i in range(6)],
                ai_message=(
                    "Tasks: Welcher Worker verarbeitet gerade was."
                    if step < 4 else "food_eaten – Futter = abgeschlossener Task!"
                ),
                score=6 + step, speed_level=3,
            ),
        ), hold=2.0)

    # ── Chapter 5: Outro (50–60s, 5 Frames) ──────────────────────────────────
    CHAPTERS.append({"t": 50.0, "title": "Outro", "description": "Zusammenfassung und nächste Schritte"})
    t = 50.0
    for step in range(5):
        push(_base_state(
            focus=FocusPane.NAVIGATION, section_id="dashboard", selected_index=0,
            status_message=":tutorial start snake_mode – starte das Tutorial",
            header_logo_game=_snake_game(
                snake=[(60 + step * 5 - i * 2, 15) for i in range(8)],
                ai_snake=[(65 + step * 5 - i * 2, 13) for i in range(6)],
                ai_message="Tour abgeschlossen! Starte ':tutorial start snake_mode' für ein geführtes Tutorial.",
                score=14,
            ),
        ), hold=2.0)

    return frames


def main() -> int:
    parser = argparse.ArgumentParser(description="Erklär-AI-Snake Splash Cast erzeugen")
    parser.add_argument("--out", default="assets/operator_tui_splash.cast", help="Ausgabepfad")
    parser.add_argument("--chapters-out", default="assets/operator_tui_splash.chapters.json", help="Chapters JSON")
    args = parser.parse_args()

    out_path = ROOT / args.out
    chapters_out = ROOT / args.chapters_out

    print(f"Erzeuge Cast ({WIDTH}×{HEIGHT}) …")
    frames = build_frames()

    total_duration = frames[-1][0] if frames else 0.0
    print(f"  Frames:   {len(frames)}")
    print(f"  Dauer:    {total_duration:.1f}s")

    if total_duration < 50:
        print(f"FEHLER: Cast zu kurz ({total_duration:.1f}s < 50s)", file=sys.stderr)
        return 1

    content = _asciinema_v2(title="Ananta Operator TUI – Erklär-AI-Snake", frames=frames)
    size_kb = len(content.encode()) / 1024
    print(f"  Größe:    {size_kb:.1f} KB")

    if size_kb > 400:
        print(f"FEHLER: Cast zu groß ({size_kb:.1f} KB > 400 KB)", file=sys.stderr)
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    print(f"  Gespeichert → {out_path}")

    # T05.02: Chapters JSON
    chapters_data = {"chapters": CHAPTERS}
    chapters_out.parent.mkdir(parents=True, exist_ok=True)
    chapters_out.write_text(json.dumps(chapters_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Chapters  → {chapters_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
