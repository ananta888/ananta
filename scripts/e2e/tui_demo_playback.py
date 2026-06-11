from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from client_surfaces.operator_tui.models import FocusPane, OperatorState, PanelState
from client_surfaces.operator_tui.renderer import render_operator_shell
from client_surfaces.operator_tui.splash_animation import build_splash_frames

from scripts.e2e.tui_demo_recorder import _asciinema_v2_lines


def _tutorial_ai_live_cast(*, run_id: str) -> str:
    frames: list[tuple[float, str]] = [
        (
            0.0,
            (
                "\u001b[2J\u001b[Hananta tui \u00b7 snake tutorial ai live\n"
                f"run_id: {run_id}\n"
                "model: google/gemma-4-e4b\n"
                "api: http://192.168.178.100:1234/v1\n\n"
                "Tutor-Snake initialisiert ...\n"
            ),
        ),
        (
            0.8,
            (
                "\u001b[2J\u001b[Hananta tui \u00b7 snake tutorial ai live\n"
                "s-ai [target=header]: Ich zeige zuerst Header, Endpoint und Auth.\n"
                "Hinweis: Ctrl+S aktiviert den Snake-Modus.\n"
            ),
        ),
        (
            1.6,
            (
                "\u001b[2J\u001b[Hananta tui \u00b7 snake tutorial ai live\n"
                "s-ai [target=nav]: Jetzt Navigation: Goals und Tasks ausw\u00e4hlen.\n"
                "Hinweis: Pfeiltasten steuern die Schlange.\n"
            ),
        ),
        (
            2.4,
            (
                "\u001b[2J\u001b[Hananta tui \u00b7 snake tutorial ai live\n"
                "s-ai [target=content]: Hier ist der Hauptinhalt der gew\u00e4hlten Section.\n"
                "Hinweis: M \u00f6ffnet Message-Mode.\n"
            ),
        ),
        (
            3.2,
            (
                "\u001b[2J\u001b[Hananta tui \u00b7 snake tutorial ai live\n"
                "s-ai [target=detail]: Im Detail erkl\u00e4re ich Inspect/Artifact Kontext.\n"
                "Hinweis: X markiert, C kopiert, V ersetzt (command line).\n"
            ),
        ),
    ]
    return _asciinema_v2_lines(title="Ananta Operator TUI \u2013 Tutorial AI Snake Live", frames=frames)


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
                "                               operator tui \u00b7 snake mode                            \n",
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
            "Schritt 3: N\u00e4chster Men\u00fcpunkt ist Goals.",
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
            "Schritt 4: Men\u00fcwechsel auf GOALS. Jetzt erkl\u00e4re ich den Abschnitt.",
            "goals-selected",
            "Was zeigt mir der Men\u00fcpunkt Goals genau?",
            "Goals zeigt Ziele, Status und Priorit\u00e4t; mit :inspect bekommst du Details.",
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
            "Starte mit active goals, dann ready, danach blocked pr\u00fcfen.",
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
            "Schritt 7: N\u00e4chster Men\u00fcpunkt TASKS, damit du Ausf\u00fchrung siehst.",
            "tasks-selected",
            "Und wo sehe ich laufende Ausf\u00fchrung?",
            "Im Men\u00fc TASKS: running, queued und Worker-Zuordnung.",
        ),
        (
            (70, 19),
            (55, 19),
            "content",
            FocusPane.CONTENT,
            "tasks",
            2,
            "Schritt 8: AI-Snake erkl\u00e4rt Task-Queue und laufende Worker.",
            "tasks-explain",
            "Welche Frage kann ich der AI-Snake hier stellen?",
            "Frag nach dem n\u00e4chsten Schritt, z.B. 'welchen Task zuerst?'.",
        ),
        (
            (68, 20),
            (57, 20),
            "detail",
            FocusPane.DETAIL,
            "tasks",
            2,
            "Schritt 9: Rechts erscheinen Detail-Hinweise f\u00fcr den aktiven Bereich.",
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
            "Schritt 11: Beispiel-Tipp: :inspect ausf\u00fchren und danach :refresh.",
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
            "tutorial_user_feed": "Erkl\u00e4re mir die TUI w\u00e4hrend ich mich bewege.",
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
            status_message=f"snake walkthrough {idx + 1}/{len(nav_walk)} \u00b7 run={run_id}",
            panel_states={section_id: PanelState.HEALTHY},
            section_payloads=payloads,
            header_logo_game=game,
        )
        screen = render_operator_shell(state, width=120, height=32)
        frames.append((base_time + idx * 0.7, "\u001b[2J\u001b[H" + screen + "\n"))
    return _asciinema_v2_lines(title="Ananta Operator TUI \u2013 Snake Mode Live", frames=frames, width=120, height=32)


def _click_select_explain_cast(*, run_id: str) -> str:
    steps = [
        (
            (90, 8), (82, 8), "dashboard", FocusPane.HEADER, 0,
            "start",
            "", "",
            [],
        ),
        (
            (70, 13), (70, 13), "goals", FocusPane.NAVIGATION, 1,
            "click-goals-nav",
            "[left-click] Goals Men\u00fcpunkt angeklickt",
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
                {"source": "ai", "text": "[click-select] AI-Schlange springt zu Goals\u2026"},
            ],
        ),
        (
            (66, 13), (70, 13), "goals", FocusPane.NAVIGATION, 1,
            "explanation-arrives",
            "",
            "",
            [
                {"source": "system", "text": "Kontext aktiv: Goals"},
                {"source": "ai", "text": "[click-explain] Goals zeigt alle aktiven Ziele. Status: active=l\u00e4uft, ready=bereit, blocked=blockiert. Mit :inspect \u00f6ffnest du Details."},
            ],
        ),
        (
            (62, 15), (65, 14), "goals", FocusPane.CONTENT, 1,
            "user-continues-chat",
            "Was bedeutet blocked genau?",
            "[click-select] [lmstudio-chat-active] Blocked = ein Goal wartet auf ein anderes Goal oder eine externe Abh\u00e4ngigkeit. Pr\u00fcfe :deps f\u00fcr den Dependency-Graph.",
            [
                {"source": "system", "text": "Kontext aktiv: Goals"},
                {"source": "ai", "text": "[click-explain] Goals zeigt alle aktiven Ziele."},
                {"source": "user", "text": "Was bedeutet blocked genau?"},
                {"source": "ai", "text": "[click-select] [lmstudio-chat-active] Blocked = wartet auf Abh\u00e4ngigkeit."},
            ],
        ),
        (
            (58, 17), (61, 16), "tasks", FocusPane.NAVIGATION, 2,
            "click-tasks-nav",
            "[left-click] Tasks angeklickt",
            "",
            [
                {"source": "system", "text": "Kontext aktiv: Tasks"},
                {"source": "ai", "text": "[click-explain] [click-select] Tasks zeigt Worker-Ausf\u00fchrungen. running=aktiv, queued=wartet."},
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
            "tutorial_user_feed": f"Erkl\u00e4re {section_id} nach Klick.",
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
            status_message=f"[click-select] {phase} \u00b7 run={run_id}",
            panel_states={section_id: PanelState.HEALTHY},
            section_payloads=payloads,
            header_logo_game=game,
        )
        screen = render_operator_shell(state, width=120, height=32)
        frames.append((base_time + idx * 0.9, "\x1b[2J\x1b[H" + screen + "\n"))

    return _asciinema_v2_lines(
        title=f"Ananta TUI \u2013 Click Select + AI Snake Explain ({run_id})",
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
    nav_walk = [
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
            "Was zeigt der Men\u00fcpunkt Goals genau? [mouse-follow] [heuristic:snake_tui_follow_distance_default]",
            "[lmstudio-chat-active] Goals zeigt Ziele, Status und Priorit\u00e4t. Mit :inspect bekommst du Details zu jedem Ziel.",
        ),
        (
            (62, 14), (52, 14), (62, 14),
            "nav", FocusPane.NAVIGATION, "goals", 1,
            "snake_tui_artifact_intent_default",
            "artifact-detected",
            "Wie priorisiere ich schnell? [mouse-follow] [heuristic:snake_tui_artifact_intent_default]",
            "[lmstudio-chat-active] Starte mit active goals, dann ready, danach blocked pr\u00fcfen.",
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
            "[lmstudio-chat-active] Im TASKS-Men\u00fc siehst du running, queued und Worker-Zuordnung.",
        ),
        (
            (56, 18), (46, 18), (56, 18),
            "content", FocusPane.CONTENT, "tasks", 2,
            "snake_tui_follow_distance_default",
            "tasks-explain",
            "Welche Frage kann ich der AI-Snake stellen? [mouse-follow]",
            "[lmstudio-chat-active] Frag nach dem n\u00e4chsten Schritt, z.B. 'welchen Task zuerst?'.",
        ),
        (
            (54, 19), (54, 19), (54, 19),
            "follow", FocusPane.CONTENT, "tasks", 2,
            "snake_tui_follow_distance_default",
            "contact",
            "OK, was jetzt konkret? [ai-fast-target] [mouse-follow]",
            "[lmstudio-chat-active] Ablauf: :inspect ausf\u00fchren, Ergebnis lesen, dann :refresh.",
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
            "tutorial_user_feed": "Erkl\u00e4re mir die TUI w\u00e4hrend ich mich bewege.",
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
                f"[heuristic:{heuristic_id}] [mouse-follow] snake step {idx + 1}/{len(nav_walk)} \u00b7 run={run_id}"
            ),
            panel_states={section_id: PanelState.HEALTHY},
            section_payloads=payloads,
            header_logo_game=game,
        )
        screen = render_operator_shell(state, width=120, height=32)
        frames.append((base_time + idx * 0.8, "\u001b[2J\u001b[H" + screen + "\n"))

    return _asciinema_v2_lines(
        title=f"Ananta TUI \u2013 Snake Heuristic + LM Studio ({run_id})",
        frames=frames,
        width=120,
        height=32,
    )


def _sync_tutorial_ai_live_cast_targets(
    *,
    cast_content: str,
    sync_targets: list[Path] | None = None,
) -> list[str]:
    targets = (
        [
            Path("tests/output/operator_tui_tutorial_ai_live.cast"),
            Path("web/www/assets/operator_tui_tutorial_ai_live.cast"),
            Path("web/www/assets/operator_tui_splash.cast"),
        ]
        if sync_targets is None
        else sync_targets
    )
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
    targets = (
        [
            Path("tests/output/operator_tui_splash.cast"),
            Path("web/www/assets/operator_tui_splash.cast"),
        ]
        if sync_targets is None
        else sync_targets
    )
    written: list[str] = []
    for target in targets:
        path = Path(target)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cast_content, encoding="utf-8")
        written.append(str(path))
    return written
