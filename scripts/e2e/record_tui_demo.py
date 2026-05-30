from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import pty
import re
import select
import shlex
import struct
import subprocess
import termios
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from client_surfaces.operator_tui.models import FocusPane, OperatorState, PanelState
from client_surfaces.operator_tui.renderer import render_operator_shell
from client_surfaces.operator_tui.splash_animation import build_splash_frames

try:
    from scripts.e2e.e2e_artifacts import write_text_artifact
except ModuleNotFoundError:
    import sys

    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from scripts.e2e.e2e_artifacts import write_text_artifact


def _asciinema_v2_lines(
    *,
    title: str,
    frames: list[tuple[float, str]],
    width: int = 104,
    height: int = 30,
) -> str:
    header = {
        "version": 2,
        "width": max(40, int(width)),
        "height": max(12, int(height)),
        "title": title,
        "env": {"TERM": "xterm-256color", "COLORTERM": "truecolor"},
    }
    lines = [json.dumps(header, ensure_ascii=False)]
    for timestamp, frame in frames:
        lines.append(json.dumps([float(timestamp), "o", frame], ensure_ascii=False))
    return "\n".join(lines) + "\n"


def _default_tui_command(*, section: str | None = None, focus: str | None = None) -> str:
    base = ".venv/bin/ananta tui" if Path(".venv/bin/ananta").exists() else "ananta tui"
    if section:
        base += f" --section {section}"
    if focus:
        base += f" --focus {focus}"
    return base


def _apply_tui_e2e_baseline_env(env: dict[str, str], *, width: int, height: int) -> dict[str, str]:
    env.setdefault("TERM", "xterm-256color")
    env.setdefault("COLORTERM", "truecolor")
    env.setdefault("COLUMNS", str(width))
    env.setdefault("LINES", str(height))
    env.setdefault("ANANTA_TUI_SPLASH", "1")
    env.setdefault("ANANTA_TUI_MOUSE", "1")
    # Shared TUI-E2E baseline: start with header logo + snake mode active.
    env.setdefault("ANANTA_TUI_HEADER_SNAKE", "1")
    env.setdefault("ANANTA_TUI_SNAKE_MODE", "1")
    return env


def _fetch_share_titles(*, endpoint: str, token: str) -> list[str]:
    req = urllib.request.Request(
        f"{endpoint.rstrip('/')}/share-sessions",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=5.0) as response:
        payload = json.loads(response.read().decode("utf-8"))

    def _extract_titles(obj: object) -> list[str]:
        if isinstance(obj, dict):
            out: list[str] = []
            title = obj.get("title")
            if isinstance(title, str) and title.strip():
                out.append(title.strip())
            for value in obj.values():
                out.extend(_extract_titles(value))
            return out
        if isinstance(obj, list):
            out: list[str] = []
            for item in obj:
                out.extend(_extract_titles(item))
            return out
        return []

    return _extract_titles(payload)


def _tutorial_ai_live_cast(*, run_id: str) -> str:
    frames: list[tuple[float, str]] = [
        (
            0.0,
            (
                "\u001b[2J\u001b[Hananta tui · snake tutorial ai live\n"
                f"run_id: {run_id}\n"
                "model: google/gemma-4-e4b\n"
                "api: http://192.168.178.100:1234/v1\n\n"
                "Tutor-Snake initialisiert ...\n"
            ),
        ),
        (
            0.8,
            (
                "\u001b[2J\u001b[Hananta tui · snake tutorial ai live\n"
                "s-ai [target=header]: Ich zeige zuerst Header, Endpoint und Auth.\n"
                "Hinweis: Ctrl+S aktiviert den Snake-Modus.\n"
            ),
        ),
        (
            1.6,
            (
                "\u001b[2J\u001b[Hananta tui · snake tutorial ai live\n"
                "s-ai [target=nav]: Jetzt Navigation: Goals und Tasks auswählen.\n"
                "Hinweis: Pfeiltasten steuern die Schlange.\n"
            ),
        ),
        (
            2.4,
            (
                "\u001b[2J\u001b[Hananta tui · snake tutorial ai live\n"
                "s-ai [target=content]: Hier ist der Hauptinhalt der gewählten Section.\n"
                "Hinweis: M öffnet Message-Mode.\n"
            ),
        ),
        (
            3.2,
            (
                "\u001b[2J\u001b[Hananta tui · snake tutorial ai live\n"
                "s-ai [target=detail]: Im Detail erkläre ich Inspect/Artifact Kontext.\n"
                "Hinweis: X markiert, C kopiert, V ersetzt (command line).\n"
            ),
        ),
    ]
    return _asciinema_v2_lines(title="Ananta Operator TUI – Tutorial AI Snake Live", frames=frames)


def _snake_mode_live_cast(*, run_id: str) -> str:
    def _load_splash_intro_from_cast(*, max_frames: int = 40) -> list[str]:
        cast_path = Path("tests/output/operator_tui_splash.cast")
        if not cast_path.exists():
            return []
        frames: list[str] = []
        for raw_line in cast_path.read_text(encoding="utf-8").splitlines()[1:]:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not (isinstance(event, list) and len(event) >= 3 and event[1] == "o" and isinstance(event[2], str)):
                continue
            frames.append(event[2])
            if len(frames) >= max_frames:
                break
        return frames

    frames: list[tuple[float, str]] = []
    splash_frames = _load_splash_intro_from_cast(max_frames=40)
    if not splash_frames:
        splash_frames = ["\u001b[2J\u001b[H" + screen + "\n" for screen in build_splash_frames(w=120, h=32, fps=12)[:40]]
    if splash_frames:
        splash_frames = splash_frames + [splash_frames[-1], splash_frames[-1], splash_frames[-1]]
        for idx, screen in enumerate(splash_frames):
            frames.append((idx * 0.12, screen))
        base_time = len(splash_frames) * 0.12 + 0.3
    else:
        frames.append(
            (
                0.0,
                "\u001b[?25l\u001b[2J\u001b[H"
                "                                      ANANTA                                      \n"
                "                               operator tui · snake mode                            \n",
            )
        )
        base_time = 0.8

    nav_walk = [
        (
            (84, 8),
            (76, 8),
            "header",
            FocusPane.HEADER,
            "dashboard",
            0,
            "Schritt 1: Wir starten im Header und lesen Endpoint/Auth.",
            "start",
            "",
            "",
        ),
        (
            (82, 10),
            (72, 10),
            "nav",
            FocusPane.NAVIGATION,
            "dashboard",
            0,
            "Schritt 2: Ich bewege mich langsam zur Navigation links.",
            "approach-nav",
            "",
            "",
        ),
        (
            (80, 12),
            (68, 12),
            "nav",
            FocusPane.NAVIGATION,
            "dashboard",
            0,
            "Schritt 3: Nächster Menüpunkt ist Goals.",
            "aim-goals",
            "",
            "",
        ),
        (
            (78, 13),
            (63, 13),
            "nav",
            FocusPane.NAVIGATION,
            "goals",
            1,
            "Schritt 4: Menüwechsel auf GOALS. Jetzt erkläre ich den Abschnitt.",
            "goals-selected",
            "Was zeigt mir der Menüpunkt Goals genau?",
            "Goals zeigt Ziele, Status und Priorität; mit :inspect bekommst du Details.",
        ),
        (
            (76, 14),
            (60, 14),
            "nav",
            FocusPane.NAVIGATION,
            "goals",
            1,
            "Schritt 5: AI-Snake zeigt, wie du Goals inspizierst und priorisierst.",
            "goals-explain",
            "Wie priorisiere ich schnell?",
            "Starte mit active goals, dann ready, danach blocked prüfen.",
        ),
        (
            (74, 16),
            (58, 16),
            "content",
            FocusPane.CONTENT,
            "goals",
            1,
            "Schritt 6: In der Mitte siehst du den GOALS-Content.",
            "goals-content",
            "",
            "",
        ),
        (
            (72, 18),
            (56, 18),
            "nav",
            FocusPane.NAVIGATION,
            "tasks",
            2,
            "Schritt 7: Nächster Menüpunkt TASKS, damit du Ausführung siehst.",
            "tasks-selected",
            "Und wo sehe ich laufende Ausführung?",
            "Im Menü TASKS: running, queued und Worker-Zuordnung.",
        ),
        (
            (70, 19),
            (55, 19),
            "content",
            FocusPane.CONTENT,
            "tasks",
            2,
            "Schritt 8: AI-Snake erklärt Task-Queue und laufende Worker.",
            "tasks-explain",
            "Welche Frage kann ich der AI-Snake hier stellen?",
            "Frag nach dem nächsten Schritt, z.B. 'welchen Task zuerst?'.",
        ),
        (
            (68, 20),
            (57, 20),
            "detail",
            FocusPane.DETAIL,
            "tasks",
            2,
            "Schritt 9: Rechts erscheinen Detail-Hinweise für den aktiven Bereich.",
            "tasks-detail",
            "",
            "",
        ),
        (
            (66, 21),
            (62, 21),
            "follow",
            FocusPane.CONTENT,
            "tasks",
            2,
            "Schritt 10: Bei Kontakt mit deiner Position gibt die AI konkrete Tipps.",
            "contact",
            "OK, was jetzt konkret?",
            "Konkreter Ablauf: :inspect, dann Ergebnis lesen, danach :refresh.",
        ),
        (
            (64, 22),
            (64, 22),
            "follow",
            FocusPane.CONTENT,
            "tasks",
            2,
            "Schritt 11: Beispiel-Tipp: :inspect ausführen und danach :refresh.",
            "contact-tip",
            "",
            "",
        ),
    ]
    history: list[dict[str, object]] = []
    for idx, (local_head, ai_head, target, focus, section_id, selected_index, text, phase, user_question, ai_answer) in enumerate(
        nav_walk
    ):
        if user_question.strip():
            history.append({"at": float(idx) + 0.01, "source": "user", "target": target, "text": user_question.strip()})
            history.append(
                {
                    "at": float(idx) + 0.02,
                    "source": "openai-compatible",
                    "target": target,
                    "text": ai_answer.strip() or text,
                }
            )
        else:
            history.append({"at": float(idx), "source": "openai-compatible", "target": target, "text": text})
        local_snake = [
            local_head,
            ((local_head[0] - 1) % 120, local_head[1]),
            ((local_head[0] - 2) % 120, local_head[1]),
            ((local_head[0] - 3) % 120, local_head[1]),
        ]
        ai_snake = [
            ai_head,
            ((ai_head[0] - 1) % 120, ai_head[1]),
            ((ai_head[0] - 2) % 120, ai_head[1]),
            ((ai_head[0] - 3) % 120, ai_head[1]),
        ]
        game = {
            "active": True,
            "alive": True,
            "ui_steering": True,
            "free_mode": True,
            "mouse_follow_enabled": True,
            "mouse_state": {"x": local_head[0], "y": local_head[1], "active": True, "event": "move"},
            "artifact_intent_confidence": "confirmed" if idx >= 5 else "weak",
            "tutorial_ai_target_mode": "fast_target" if idx >= 5 else "follow_user",
            "artifact_chat_state": {
                "active_target": {
                    "label": "Artifacts row" if idx >= 5 else "",
                    "path": "tests/output/operator_tui_splash.cast" if idx >= 5 else "",
                    "id": "artifact-cast",
                }
                if idx >= 5
                else None,
                "messages": [],
                "backend_source": "openai-compatible",
            },
            "tutorial_mode": True,
            "local_snake_id": "s1",
            "snake": local_snake,
            "trail_path": list(local_snake),
            "message": f"user-snake frame {idx + 1}",
            "message_style": "ticker",
            "snake_color": "mint",
            "tutorial_user_feed": "Erkläre mir die TUI während ich mich bewege.",
            "tutorial_ai_local_contact": bool(target == "follow"),
            "tutorial_ai_contact_zone": "content" if target == "follow" else target,
            "tutorial_propose_history": history[-8:],
            "snakes": {
                "s1": {
                    "id": "s1",
                    "pseudonym": "local-snake",
                    "oidc_provider": "local",
                    "snake": local_snake,
                    "trail_path": list(local_snake),
                    "message": f"user navigates {phase}",
                    "message_style": "trail",
                    "snake_color": "mint",
                    "local": True,
                },
                "s-ai": {
                    "id": "s-ai",
                    "pseudonym": "tutor-ai",
                    "oidc_provider": "codecompass-ai",
                    "snake": ai_snake,
                    "trail_path": list(ai_snake),
                    "message": text,
                    "message_style": "ticker",
                    "snake_color": "amber",
                    "local": False,
                    "target_cell": ai_head,
                },
            },
        }
        payloads = {
            "dashboard": {
                "agents": {"online": 3, "total": 3},
                "queue": {"depth": 2},
                "goal_summary": "1 active goal",
                "task_summary": "2 running tasks",
            },
            "goals": {
                "items": [
                    {"id": "g-ui", "title": "Improve TUI onboarding", "status": "active"},
                    {"id": "g-snakes", "title": "Explain AI-snake guidance", "status": "ready"},
                ]
            },
            "tasks": {
                "running": 2,
                "queued": 3,
                "summary": "worker-1 processes goal g-ui; worker-2 validates cast",
            },
        }
        state = OperatorState(
            endpoint="http://localhost:5000",
            focus=focus,
            section_id=section_id,
            selected_index=selected_index,
            status_message=f"snake walkthrough {idx + 1}/{len(nav_walk)} · run={run_id}",
            panel_states={section_id: PanelState.HEALTHY},
            section_payloads=payloads,
            header_logo_game=game,
        )
        screen = render_operator_shell(state, width=120, height=32)
        frames.append((base_time + idx * 0.7, "\u001b[2J\u001b[H" + screen + "\n"))
    return _asciinema_v2_lines(title="Ananta Operator TUI – Snake Mode Live", frames=frames, width=120, height=32)


def _snake_mode_live_e2e_cast(*, run_id: str) -> str:
    width = max(80, min(220, int(os.environ.get("ANANTA_TUI_E2E_CAST_WIDTH", "120"))))
    height = max(20, min(80, int(os.environ.get("ANANTA_TUI_E2E_CAST_HEIGHT", "32"))))
    duration_limit = max(10.0, min(120.0, float(os.environ.get("ANANTA_TUI_E2E_CAST_SECONDS", "52"))))
    default_cmd = _default_tui_command()
    run_command = str(os.environ.get("ANANTA_TUI_E2E_CAST_COMMAND") or default_cmd).strip()
    command = shlex.split(run_command)
    if not command:
        raise RuntimeError("ANANTA_TUI_E2E_CAST_COMMAND is empty")

    env = _apply_tui_e2e_baseline_env(dict(os.environ), width=width, height=height)
    env.setdefault("ANANTA_TUI_SNAKE_TUTORIAL_AI", "1")
    env.setdefault("ANANTA_TUI_AUTO_BUILD_CODECOMPASS", "1")
    env.setdefault("ANANTA_TUI_SNAKE_AI_BACKEND", "openai-compatible")
    env.setdefault("ANANTA_TUI_SNAKE_AI_REFRESH", "1.2")
    env.setdefault("ANANTA_TUI_SNAKE_AI_TIMEOUT", "8.0")
    env.setdefault("ANANTA_TUI_SNAKE_SELECT_DELAY", "0.30")
    env.setdefault("ANANTA_TUI_SNAKE_AI_MODEL", str(env.get("ANANTA_TUI_LLM_MODEL") or "meta-llama_-_llama-3.2-1b-instruct"))
    env.setdefault("ANANTA_TUI_SNAKE_AI_API_BASE_URL", str(env.get("ANANTA_TUI_LLM_API_BASE") or "http://127.0.0.1:1234/v1"))
    env.setdefault("ANANTA_TUI_SNAKE_AI_API_TOKEN", str(env.get("ANANTA_TUI_LLM_API_TOKEN") or ""))

    # Event-driven walkthrough:
    # wait for usable TUI frame -> snake to nav/artifacts -> tutorial AI -> user asks -> AI answers -> quit.
    script_actions: list[dict[str, object]] = [
        {"at": 4.4, "need": "", "send": b"o"},  # mouse follow on/off toggle hint
        {"at": 4.8, "need": "", "send": b"\x1b[<35;35;12M"},  # synthetic mouse move (SGR)
        {"at": 5.0, "need": "", "send": (b"\x1b[B" * 12)},
        {"at": 6.4, "need": "", "send": b" "},  # brake near nav menu rows
        {"at": 6.9, "need": "", "send": b"\x13"},  # snake off
        {"at": 7.5, "need": "", "send": b"jjj\r"},  # select/open Artifacts in NAV
        {"at": 9.0, "need": "", "send": b"\x13"},  # snake on again
        {"at": 9.6, "need": "", "send": b"u"},  # enable tutorial ai
        {"at": 11.0, "need": "", "send": b"m"},
        {"at": 11.3, "need": "", "send": "Erkläre den Menüpunkt Artifacts in diesem Projekt.".encode("utf-8")},
        {"at": 11.8, "need": "", "send": b"\r"},
        {"at": 16.0, "need": "", "send": b"m"},
        {
            "at": 16.3,
            "need": "",
            "send": "[mouse-follow] [artifact-intent] [ai-fast-target] [artifact-chat-active] Welche Cast- und Bericht-Artefakte sind hier wichtig?".encode(
                "utf-8"
            ),
        },
        {"at": 16.8, "need": "", "send": b"\r"},
        {"at": 24.0, "need": "", "send": (b"\x1b[C" * 4 + b"\x1b[B" * 2 + b" ")},
        {"at": 36.0, "need": "[openai-compatible->", "send": b"q"},
        {"at": 50.0, "need": "", "send": b"q"},
    ]

    master_fd, slave_fd = pty.openpty()
    try:
        # force deterministic terminal size for prompt_toolkit session
        termios_winsz = struct.pack("HHHH", height, width, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, termios_winsz)
    except Exception:
        pass

    process = subprocess.Popen(
        command,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        env=env,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave_fd)

    events: list[tuple[float, str]] = []
    action_index = 0
    started = time.monotonic()
    forced_quit_sent = False
    ansi_re = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b.")
    text_tail = ""

    try:
        while True:
            elapsed = time.monotonic() - started
            while action_index < len(script_actions):
                action = script_actions[action_index]
                at = float(action.get("at") or 0.0)
                need = str(action.get("need") or "")
                if elapsed < at:
                    break
                if need and need not in text_tail:
                    break
                payload = action.get("send")
                if isinstance(payload, bytes):
                    os.write(master_fd, payload)
                action_index += 1

            readable, _, _ = select.select([master_fd], [], [], 0.08)
            if readable:
                try:
                    chunk = os.read(master_fd, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        chunk = b""
                    else:
                        raise
                if chunk:
                    text = chunk.decode("utf-8", errors="replace")
                    plain = ansi_re.sub("", text)
                    text_tail = (text_tail + plain)[-12000:]
                    if events:
                        events.append((elapsed, text))
                    else:
                        events.append((elapsed, "\x1b[2J\x1b[H" + text))
                elif process.poll() is not None:
                    break

            if process.poll() is not None:
                # drain remaining output
                try:
                    while True:
                        chunk = os.read(master_fd, 65536)
                        if not chunk:
                            break
                        events.append((time.monotonic() - started, chunk.decode("utf-8", errors="replace")))
                except OSError:
                    pass
                break

            if elapsed >= duration_limit and not forced_quit_sent:
                os.write(master_fd, b"q")
                forced_quit_sent = True

            if elapsed >= (duration_limit + 4.0):
                break
    finally:
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=1.0)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        try:
            os.close(master_fd)
        except OSError:
            pass

    if not events:
        raise RuntimeError(
            "No PTY output captured for snake-mode-live-e2e cast. "
            "Check ANANTA_TUI_E2E_CAST_COMMAND and local terminal environment."
        )

    # normalize timestamps to monotonic cast timeline
    first_ts = events[0][0]
    normalized = [(max(0.0, ts - first_ts), frame) for ts, frame in events]
    return _asciinema_v2_lines(
        title=f"Ananta Operator TUI – Snake Mode Live E2E ({run_id})",
        frames=normalized,
        width=width,
        height=height,
    )


def _share_session_live_e2e_cast(*, run_id: str) -> str:
    width = max(
        80,
        min(
            220,
            int(
                os.environ.get("ANANTA_TUI_E2E_SHARE_CAST_WIDTH")
                or os.environ.get("ANANTA_TUI_E2E_CAST_WIDTH")
                or "200"
            ),
        ),
    )
    height = max(
        20,
        min(
            80,
            int(
                os.environ.get("ANANTA_TUI_E2E_SHARE_CAST_HEIGHT")
                or os.environ.get("ANANTA_TUI_E2E_CAST_HEIGHT")
                or "56"
            ),
        ),
    )
    duration_limit = max(10.0, min(120.0, float(os.environ.get("ANANTA_TUI_E2E_CAST_SECONDS", "34"))))
    default_cmd = _default_tui_command(section="share", focus="navigation")
    run_command = str(os.environ.get("ANANTA_TUI_E2E_CAST_COMMAND") or default_cmd).strip()
    command = shlex.split(run_command)
    if not command:
        raise RuntimeError("ANANTA_TUI_E2E_CAST_COMMAND is empty")

    endpoint = str(
        os.environ.get("ANANTA_TUI_E2E_SHARE_ENDPOINT")
        or os.environ.get("ANANTA_ENDPOINT")
        or os.environ.get("ANANTA_HUB_URL")
        or "http://localhost:5000"
    ).strip()
    env = _apply_tui_e2e_baseline_env(dict(os.environ), width=width, height=height)
    env["ANANTA_ENDPOINT"] = endpoint
    env["ANANTA_BASE_URL"] = endpoint
    env["ANANTA_HUB_URL"] = endpoint
    env["ANANTA_TUI_SNAKE_TUTORIAL_AI"] = "0"
    env["ANANTA_TUI_E2E_SHARE_AUTORUN"] = "0"
    env["ANANTA_TUI_E2E_SHARE_ONLY_NAV"] = "1"
    title = str(os.environ.get("ANANTA_TUI_E2E_SHARE_TITLE") or "e2e-share").strip() or "e2e-share"

    script_actions: list[dict[str, object]] = [
        {"at": 2.2, "send": f":share create {title}\r".encode("utf-8")},
        {"at": 5.5, "send": b":share list\r"},
        {"at": 8.8, "send": b":share list\r"},
        {"at": 10.0, "send": b"\x1f"},  # Ctrl+_ => save_tui_snapshot
        {"at": 38.0, "send": b"q"},
    ]

    master_fd, slave_fd = pty.openpty()
    try:
        termios_winsz = struct.pack("HHHH", height, width, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, termios_winsz)
    except Exception:
        pass

    process = subprocess.Popen(
        command,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        env=env,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave_fd)

    events: list[tuple[float, str]] = []
    action_index = 0
    started = time.monotonic()
    forced_quit_sent = False
    ansi_re = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b.")
    text_tail = ""

    try:
        while True:
            elapsed = time.monotonic() - started
            while action_index < len(script_actions):
                action = script_actions[action_index]
                at = float(action.get("at") or 0.0)
                need = str(action.get("need") or "")
                if elapsed < at:
                    break
                if need and need not in text_tail:
                    break
                payload = action.get("send")
                if isinstance(payload, bytes):
                    os.write(master_fd, payload)
                action_index += 1

            readable, _, _ = select.select([master_fd], [], [], 0.08)
            if readable:
                try:
                    chunk = os.read(master_fd, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        chunk = b""
                    else:
                        raise
                if chunk:
                    text = chunk.decode("utf-8", errors="replace")
                    plain = ansi_re.sub("", text)
                    text_tail = (text_tail + plain)[-12000:]
                    if events:
                        events.append((elapsed, text))
                    else:
                        events.append((elapsed, "\x1b[2J\x1b[H" + text))
                elif process.poll() is not None:
                    break

            if process.poll() is not None:
                try:
                    while True:
                        chunk = os.read(master_fd, 65536)
                        if not chunk:
                            break
                        events.append((time.monotonic() - started, chunk.decode("utf-8", errors="replace")))
                except OSError:
                    pass
                break

            if elapsed >= duration_limit and not forced_quit_sent:
                os.write(master_fd, b"q")
                forced_quit_sent = True

            if elapsed >= (duration_limit + 4.0):
                break
    finally:
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=1.0)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        try:
            os.close(master_fd)
        except OSError:
            pass

    if not events:
        raise RuntimeError(
            "No PTY output captured for share-session-live-e2e cast. "
            "Check ANANTA_TUI_E2E_CAST_COMMAND and local terminal environment."
        )

    first_ts = events[0][0]
    normalized = [(max(0.0, ts - first_ts), frame) for ts, frame in events]

    token = str(env.get("ANANTA_AUTH_TOKEN") or "").strip()
    if token:
        try:
            titles = _fetch_share_titles(endpoint=endpoint, token=token)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
            titles = []
        if titles:
            summary = (
                "\x1b[2J\x1b[H"
                "share-session-live-e2e summary\n"
                f"endpoint: {endpoint}\n"
                f"titles: {', '.join(sorted(set(titles))[:6])}\n"
                f"count: {len(titles)}\n"
            )
            normalized.append((normalized[-1][0] + 0.35, summary))

    return _asciinema_v2_lines(
        title=f"Ananta Operator TUI – Share Session Live E2E ({run_id})",
        frames=normalized,
        width=width,
        height=height,
    )


def _click_select_explain_cast(*, run_id: str) -> str:
    """Rendered cast: left-click selects item → AI snake jumps → chat opens → explanation appears."""
    steps = [
        # (local_head, ai_head, section_id, focus, sel_idx, phase, user_q, ai_answer, chat_msgs)
        (
            (90, 8), (82, 8), "dashboard", FocusPane.HEADER, 0,
            "start",
            "", "",
            [],
        ),
        (
            (70, 13), (70, 13), "goals", FocusPane.NAVIGATION, 1,
            "click-goals-nav",
            "[left-click] Goals Menüpunkt angeklickt",
            "",
            [{"source": "system", "text": "Kontext aktiv: Goals"}],
        ),
        (
            (68, 13), (70, 13), "goals", FocusPane.NAVIGATION, 1,
            "ai-snake-jumps",
            "",
            "",
            [
                {"source": "system", "text": "Kontext aktiv: Goals"},
                {"source": "ai", "text": "[click-select] AI-Schlange springt zu Goals…"},
            ],
        ),
        (
            (66, 13), (70, 13), "goals", FocusPane.NAVIGATION, 1,
            "explanation-arrives",
            "",
            "",
            [
                {"source": "system", "text": "Kontext aktiv: Goals"},
                {"source": "ai", "text": "[click-explain] Goals zeigt alle aktiven Ziele. Status: active=läuft, ready=bereit, blocked=blockiert. Mit :inspect öffnest du Details."},
            ],
        ),
        (
            (62, 15), (65, 14), "goals", FocusPane.CONTENT, 1,
            "user-continues-chat",
            "Was bedeutet blocked genau?",
            "[click-select] [lmstudio-chat-active] Blocked = ein Goal wartet auf ein anderes Goal oder eine externe Abhängigkeit. Prüfe :deps für den Dependency-Graph.",
            [
                {"source": "system", "text": "Kontext aktiv: Goals"},
                {"source": "ai", "text": "[click-explain] Goals zeigt alle aktiven Ziele."},
                {"source": "user", "text": "Was bedeutet blocked genau?"},
                {"source": "ai", "text": "[click-select] [lmstudio-chat-active] Blocked = wartet auf Abhängigkeit."},
            ],
        ),
        (
            (58, 17), (61, 16), "tasks", FocusPane.NAVIGATION, 2,
            "click-tasks-nav",
            "[left-click] Tasks angeklickt",
            "",
            [
                {"source": "system", "text": "Kontext aktiv: Tasks"},
                {"source": "ai", "text": "[click-explain] [click-select] Tasks zeigt Worker-Ausführungen. running=aktiv, queued=wartet."},
            ],
        ),
    ]

    frames: list[tuple[float, str]] = []
    base_time = 0.4

    for idx, (local_head, ai_head, section_id, focus, sel_idx, phase, user_q, ai_answer, chat_msgs) in enumerate(steps):
        local_snake = [
            local_head,
            ((local_head[0] - 1) % 120, local_head[1]),
            ((local_head[0] - 2) % 120, local_head[1]),
            ((local_head[0] - 3) % 120, local_head[1]),
        ]
        ai_snake = [
            ai_head,
            ((ai_head[0] - 1) % 120, ai_head[1]),
            ((ai_head[0] - 2) % 120, ai_head[1]),
            ((ai_head[0] - 3) % 120, ai_head[1]),
        ]

        messages = list(chat_msgs)
        if user_q:
            messages = [*messages, {"source": "user", "text": user_q}]
        if ai_answer:
            messages = [*messages, {"source": "ai", "text": ai_answer}]

        game = {
            "active": True,
            "alive": True,
            "ui_steering": True,
            "free_mode": True,
            "mouse_follow_enabled": True,
            "movement_mode": "mouse_follow",
            "mouse_state": {"x": local_head[0], "y": local_head[1], "active": True, "event": "down"},
            "artifact_intent_confidence": "confirmed" if idx >= 1 else "none",
            "tutorial_ai_target_mode": "fast_target" if idx >= 1 else "follow_user",
            "active_heuristic_id": "snake_tui_follow_distance_default",
            "artifact_intent_target": {"label": "Goals" if section_id == "goals" else "Tasks", "pane": "nav"} if idx >= 1 else None,
            "artifact_chat_state": {
                "active_target": {"label": "Goals" if section_id == "goals" else ("Tasks" if section_id == "tasks" else ""), "id": f"{section_id}-click"},
                "messages": messages[-8:],
                "backend_source": "openai-compatible",
            } if messages else None,
            "tutorial_mode": True,
            "local_snake_id": "s1",
            "snake": local_snake,
            "trail_path": list(local_snake),
            "message": f"[left-click] {phase}",
            "message_style": "ticker",
            "snake_color": "mint",
            "tutorial_user_feed": f"Erkläre {section_id} nach Klick.",
            "tutorial_ai_local_contact": idx >= 1,
            "tutorial_ai_contact_zone": "nav",
            "tutorial_propose_history": [{"at": float(idx), "source": "system", "text": f"[click-select] {phase}"}],
            "snakes": {
                "s1": {
                    "id": "s1", "pseudonym": "local-snake", "oidc_provider": "local",
                    "snake": local_snake, "trail_path": list(local_snake),
                    "message": f"[left-click] {phase}", "message_style": "trail",
                    "snake_color": "mint", "local": True,
                },
                "s-ai": {
                    "id": "s-ai", "pseudonym": "tutor-ai", "oidc_provider": "lmstudio-ai",
                    "snake": ai_snake, "trail_path": list(ai_snake),
                    "message": f"[click-explain] {phase}", "message_style": "ticker",
                    "snake_color": "amber", "local": False,
                    "target_cell": ai_head,
                },
            },
        }

        payloads = {
            "dashboard": {"agents": {"online": 2, "total": 2}, "queue": {"depth": 1}},
            "goals": {"items": [
                {"id": "g-ui", "title": "Improve TUI onboarding", "status": "active"},
                {"id": "g-snakes", "title": "Click-select AI guidance", "status": "ready"},
            ]},
            "tasks": {"running": 2, "queued": 1, "summary": "worker-1 processes g-ui"},
        }

        state = OperatorState(
            endpoint="http://localhost:1234",
            focus=focus,
            section_id=section_id,
            selected_index=sel_idx,
            status_message=f"[click-select] {phase} · run={run_id}",
            panel_states={section_id: PanelState.HEALTHY},
            section_payloads=payloads,
            header_logo_game=game,
        )
        screen = render_operator_shell(state, width=120, height=32)
        frames.append((base_time + idx * 0.9, "\x1b[2J\x1b[H" + screen + "\n"))

    return _asciinema_v2_lines(
        title=f"Ananta TUI – Click Select + AI Snake Explain ({run_id})",
        frames=frames,
        width=120,
        height=32,
    )


def _share_session_e2e_cast(*, run_id: str) -> str:
    session_id = "share-test-001"
    frames: list[tuple[float, str]] = [
        (
            0.0,
            (
                "ananta tui - share session e2e\n"
                f"run_id: {run_id}\n"
                "endpoint: http://localhost:5000\n"
                "user: admin\n\n"
                "ready> :share help\n"
                "commands: create, list, join, leave, debug\n\n"
            ),
        ),
        (
            1.2,
            (
                "ready> :share create test\n"
                f"[share-create] status=ok session_id={session_id} name=test\n"
                f"[share-create] join_url=/share/{session_id}\n"
                "ready> \n"
            ),
        ),
        (
            2.4,
            (
                "ready> :share list\n"
                "[share-list] count=1\n"
                "id              name   owner  state   participants\n"
                f"{session_id}  test   admin  active  1\n"
                "ready> \n"
            ),
        ),
        (
            3.6,
            (
                "ready> :share list\n"
                "[share-list] visible session from previous create command\n"
                "ready> q\n"
            ),
        ),
    ]
    return _asciinema_v2_lines(
        title=f"Ananta Operator TUI - Share Session E2E ({run_id})",
        frames=frames,
        width=120,
        height=32,
    )


def _snake_lmstudio_heuristic_cast(*, run_id: str) -> str:
    """Rendered cast: mouse-follow + heuristic switching + LM Studio chat response."""
    nav_walk = [
        # (local_head, ai_head, mouse_pos, target, focus, section_id, sel_idx, heuristic_id, phase, user_q, ai_answer)
        (
            (90, 8), (80, 8), (90, 8),
            "header", FocusPane.HEADER, "dashboard", 0,
            "snake_tui_follow_distance_default",
            "start",
            "",
            "",
        ),
        (
            (82, 10), (74, 10), (82, 10),
            "nav", FocusPane.NAVIGATION, "dashboard", 0,
            "snake_tui_follow_distance_default",
            "mouse-follow-left",
            "",
            "",
        ),
        (
            (70, 12), (62, 12), (70, 12),
            "nav", FocusPane.NAVIGATION, "dashboard", 0,
            "snake_tui_follow_distance_default",
            "approach-nav",
            "",
            "",
        ),
        (
            (65, 13), (55, 13), (65, 13),
            "nav", FocusPane.NAVIGATION, "goals", 1,
            "snake_tui_follow_distance_default",
            "goals-selected",
            "Was zeigt der Menüpunkt Goals genau? [mouse-follow] [heuristic:snake_tui_follow_distance_default]",
            "[lmstudio-chat-active] Goals zeigt Ziele, Status und Priorität. Mit :inspect bekommst du Details zu jedem Ziel.",
        ),
        (
            (62, 14), (52, 14), (62, 14),
            "nav", FocusPane.NAVIGATION, "goals", 1,
            "snake_tui_artifact_intent_default",
            "artifact-detected",
            "Wie priorisiere ich schnell? [mouse-follow] [heuristic:snake_tui_artifact_intent_default]",
            "[lmstudio-chat-active] Starte mit active goals, dann ready, danach blocked prüfen.",
        ),
        (
            (60, 15), (50, 15), (60, 15),
            "content", FocusPane.CONTENT, "goals", 1,
            "snake_tui_artifact_intent_default",
            "goals-content",
            "",
            "",
        ),
        (
            (58, 16), (48, 16), (58, 16),
            "nav", FocusPane.NAVIGATION, "tasks", 2,
            "snake_tui_follow_distance_default",
            "tasks-selected",
            "Wo sehe ich laufende Tasks? [mouse-follow] [heuristic:snake_tui_follow_distance_default]",
            "[lmstudio-chat-active] Im TASKS-Menü siehst du running, queued und Worker-Zuordnung.",
        ),
        (
            (56, 18), (46, 18), (56, 18),
            "content", FocusPane.CONTENT, "tasks", 2,
            "snake_tui_follow_distance_default",
            "tasks-explain",
            "Welche Frage kann ich der AI-Snake stellen? [mouse-follow]",
            "[lmstudio-chat-active] Frag nach dem nächsten Schritt, z.B. 'welchen Task zuerst?'.",
        ),
        (
            (54, 19), (54, 19), (54, 19),
            "follow", FocusPane.CONTENT, "tasks", 2,
            "snake_tui_follow_distance_default",
            "contact",
            "OK, was jetzt konkret? [ai-fast-target] [mouse-follow]",
            "[lmstudio-chat-active] Ablauf: :inspect ausführen, Ergebnis lesen, dann :refresh.",
        ),
    ]

    frames: list[tuple[float, str]] = []
    history: list[dict[str, object]] = []
    base_time = 0.4

    for idx, (local_head, ai_head, mouse_pos, target, focus, section_id, sel_idx, heuristic_id, phase, user_q, ai_answer) in enumerate(nav_walk):
        if user_q.strip():
            history.append({"at": float(idx) + 0.01, "source": "user", "target": target, "text": user_q.strip()})
            history.append({
                "at": float(idx) + 0.02,
                "source": "openai-compatible",
                "target": target,
                "text": ai_answer.strip(),
            })
        else:
            history.append({"at": float(idx), "source": "openai-compatible", "target": target, "text": f"[mouse-follow] Schritt {idx + 1}: {phase}"})

        local_snake = [
            local_head,
            ((local_head[0] - 1) % 120, local_head[1]),
            ((local_head[0] - 2) % 120, local_head[1]),
            ((local_head[0] - 3) % 120, local_head[1]),
        ]
        ai_snake = [
            ai_head,
            ((ai_head[0] - 1) % 120, ai_head[1]),
            ((ai_head[0] - 2) % 120, ai_head[1]),
            ((ai_head[0] - 3) % 120, ai_head[1]),
        ]

        has_artifact = idx >= 4
        ai_mode = "fast_target" if idx >= 4 else "follow_user"

        game = {
            "active": True,
            "alive": True,
            "ui_steering": True,
            "free_mode": True,
            "mouse_follow_enabled": True,
            "movement_mode": "mouse_follow",
            "mouse_state": {"x": mouse_pos[0], "y": mouse_pos[1], "active": True, "event": "move"},
            "artifact_intent_confidence": "confirmed" if has_artifact else "weak",
            "tutorial_ai_target_mode": ai_mode,
            "active_heuristic_id": heuristic_id,
            "artifact_intent_target": {"label": "Goals artifact", "id": f"g-{idx}"} if has_artifact else None,
            "artifact_chat_state": {
                "active_target": {
                    "label": "Goals row",
                    "path": "tests/output/operator_tui_splash.cast",
                    "id": "artifact-cast",
                } if has_artifact else None,
                "messages": [],
                "backend_source": "openai-compatible",
            },
            "tutorial_mode": True,
            "local_snake_id": "s1",
            "snake": local_snake,
            "trail_path": list(local_snake),
            "message": f"[mouse-follow] frame {idx + 1}",
            "message_style": "ticker",
            "snake_color": "mint",
            "tutorial_user_feed": "Erkläre mir die TUI während ich mich bewege.",
            "tutorial_ai_local_contact": bool(target == "follow"),
            "tutorial_ai_contact_zone": "content" if target == "follow" else target,
            "tutorial_propose_history": history[-8:],
            "snakes": {
                "s1": {
                    "id": "s1",
                    "pseudonym": "local-snake",
                    "oidc_provider": "local",
                    "snake": local_snake,
                    "trail_path": list(local_snake),
                    "message": f"[mouse-follow] user {phase}",
                    "message_style": "trail",
                    "snake_color": "mint",
                    "local": True,
                },
                "s-ai": {
                    "id": "s-ai",
                    "pseudonym": "tutor-ai",
                    "oidc_provider": "lmstudio-ai",
                    "snake": ai_snake,
                    "trail_path": list(ai_snake),
                    "message": f"[heuristic:{heuristic_id}] {phase}",
                    "message_style": "ticker",
                    "snake_color": "amber",
                    "local": False,
                    "target_cell": ai_head,
                },
            },
        }

        payloads = {
            "dashboard": {
                "agents": {"online": 2, "total": 2},
                "queue": {"depth": 1},
                "goal_summary": "1 active goal",
                "task_summary": "2 running tasks",
            },
            "goals": {
                "items": [
                    {"id": "g-ui", "title": "Improve TUI onboarding", "status": "active"},
                    {"id": "g-snakes", "title": "Heuristic snake guidance", "status": "ready"},
                ]
            },
            "tasks": {
                "running": 2,
                "queued": 1,
                "summary": "worker-1 processes g-ui; worker-2 validates heuristics",
            },
        }

        state = OperatorState(
            endpoint="http://localhost:1234",
            focus=focus,
            section_id=section_id,
            selected_index=sel_idx,
            status_message=(
                f"[heuristic:{heuristic_id}] [mouse-follow] snake step {idx + 1}/{len(nav_walk)} · run={run_id}"
            ),
            panel_states={section_id: PanelState.HEALTHY},
            section_payloads=payloads,
            header_logo_game=game,
        )
        screen = render_operator_shell(state, width=120, height=32)
        frames.append((base_time + idx * 0.8, "[2J[H" + screen + "\n"))

    return _asciinema_v2_lines(
        title=f"Ananta TUI – Snake Heuristic + LM Studio ({run_id})",
        frames=frames,
        width=120,
        height=32,
    )


def _sync_tutorial_ai_live_cast_targets(
    *,
    cast_content: str,
    sync_targets: list[Path] | None = None,
) -> list[str]:
    targets = sync_targets or [
        Path("tests/output/operator_tui_tutorial_ai_live.cast"),
        Path("web/www/assets/operator_tui_tutorial_ai_live.cast"),
        Path("web/www/assets/operator_tui_splash.cast"),
    ]
    written: list[str] = []
    for target in targets:
        path = Path(target)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cast_content, encoding="utf-8")
        written.append(str(path))
    return written


def _sync_snake_mode_live_cast_targets(
    *,
    cast_content: str,
    sync_targets: list[Path] | None = None,
) -> list[str]:
    targets = sync_targets or [
        Path("tests/output/operator_tui_splash.cast"),
        Path("web/www/assets/operator_tui_splash.cast"),
    ]
    written: list[str] = []
    for target in targets:
        path = Path(target)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cast_content, encoding="utf-8")
        written.append(str(path))
    return written


def record_tui_demo(
    *,
    run_id: str,
    flow_id: str = "tui-demo-video",
    enabled: bool = False,
    scene: str = "default",
    sync_targets: list[Path] | None = None,
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    if not enabled:
        return {
            "status": "skipped",
            "optional": True,
            "reason": "video capture disabled (set --enable to record)",
            "video_ref": "",
        }
    normalized_scene = str(scene).strip().lower()
    if normalized_scene == "tutorial-ai-live":
        cast_content = _tutorial_ai_live_cast(run_id=run_id)
        file_name = "video-tui-tutorial-ai-live.cast"
        synced_targets = _sync_tutorial_ai_live_cast_targets(cast_content=cast_content, sync_targets=sync_targets)
    elif normalized_scene == "snake-mode-live":
        cast_content = _snake_mode_live_cast(run_id=run_id)
        file_name = "video-tui-snake-mode-live.cast"
        synced_targets = _sync_snake_mode_live_cast_targets(cast_content=cast_content, sync_targets=sync_targets)
    elif normalized_scene == "snake-mode-live-e2e":
        cast_content = _snake_mode_live_e2e_cast(run_id=run_id)
        file_name = "video-tui-snake-mode-live-e2e.cast"
        synced_targets = _sync_snake_mode_live_cast_targets(cast_content=cast_content, sync_targets=sync_targets)
    elif normalized_scene == "snake-lmstudio-heuristic":
        cast_content = _snake_lmstudio_heuristic_cast(run_id=run_id)
        file_name = "video-tui-snake-lmstudio-heuristic.cast"
        synced_targets = _sync_snake_mode_live_cast_targets(cast_content=cast_content, sync_targets=sync_targets)
    elif normalized_scene == "click-select-explain":
        cast_content = _click_select_explain_cast(run_id=run_id)
        file_name = "video-tui-click-select-explain.cast"
        synced_targets = _sync_snake_mode_live_cast_targets(cast_content=cast_content, sync_targets=sync_targets)
    elif normalized_scene == "share-session-e2e":
        cast_content = _share_session_e2e_cast(run_id=run_id)
        file_name = "video-tui-share-session-e2e.cast"
        synced_targets = (
            _sync_snake_mode_live_cast_targets(cast_content=cast_content, sync_targets=sync_targets)
            if sync_targets
            else []
        )
    elif normalized_scene == "share-session-live-e2e":
        cast_content = _share_session_live_e2e_cast(run_id=run_id)
        file_name = "video-tui-share-session-live-e2e.cast"
        synced_targets = []
    else:
        cast_content = _asciinema_v2_lines(
            title="Ananta Operator TUI – Demo Placeholder",
            frames=[(0.0, "\u001b[2J\u001b[Hananta tui demo placeholder\n")],
        )
        file_name = "video-tui-demo.cast"
        synced_targets = []
    video_ref = write_text_artifact(
        run_id,
        flow_id,
        file_name,
        cast_content,
        artifact_root=artifact_root,
    )
    return {
        "status": "recorded",
        "optional": True,
        "reason": "",
        "video_ref": video_ref,
        "synced_cast_targets": synced_targets,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Record optional TUI demo evidence.")
    parser.add_argument("--run-id", default="e2e-tui-demo")
    parser.add_argument("--flow-id", default="tui-demo-video")
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--scene", default="default")
    parser.add_argument("--artifact-root", default="")
    args = parser.parse_args()

    artifact_root = Path(args.artifact_root) if args.artifact_root else None
    payload = record_tui_demo(
        run_id=args.run_id,
        flow_id=args.flow_id,
        enabled=args.enable,
        scene=args.scene,
        artifact_root=artifact_root,
    )
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
