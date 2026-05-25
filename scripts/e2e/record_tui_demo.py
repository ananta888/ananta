from __future__ import annotations

import argparse
import json
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


def _asciinema_v2_lines(*, title: str, frames: list[tuple[float, str]]) -> str:
    header = {
        "version": 2,
        "width": 104,
        "height": 30,
        "title": title,
        "env": {"TERM": "xterm-256color", "COLORTERM": "truecolor"},
    }
    lines = [json.dumps(header, ensure_ascii=False)]
    for timestamp, frame in frames:
        lines.append(json.dumps([float(timestamp), "o", frame], ensure_ascii=False))
    return "\n".join(lines) + "\n"


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
            "Schritt 1: Wir starten im Header und lesen Endpoint/Auth.",
            "start",
        ),
        (
            (82, 10),
            (72, 10),
            "nav",
            FocusPane.NAVIGATION,
            "dashboard",
            "Schritt 2: Ich bewege mich langsam zur Navigation links.",
            "approach-nav",
        ),
        (
            (80, 12),
            (68, 12),
            "nav",
            FocusPane.NAVIGATION,
            "dashboard",
            "Schritt 3: Nächster Menüpunkt ist Goals.",
            "aim-goals",
        ),
        (
            (78, 13),
            (63, 13),
            "nav",
            FocusPane.NAVIGATION,
            "goals",
            "Schritt 4: Menüwechsel auf GOALS. Jetzt erkläre ich den Abschnitt.",
            "goals-selected",
        ),
        (
            (76, 14),
            (60, 14),
            "nav",
            FocusPane.NAVIGATION,
            "goals",
            "Schritt 5: AI-Snake zeigt, wie du Goals inspizierst und priorisierst.",
            "goals-explain",
        ),
        (
            (74, 16),
            (58, 16),
            "content",
            FocusPane.CONTENT,
            "goals",
            "Schritt 6: In der Mitte siehst du den GOALS-Content.",
            "goals-content",
        ),
        (
            (72, 18),
            (56, 18),
            "nav",
            FocusPane.NAVIGATION,
            "tasks",
            "Schritt 7: Nächster Menüpunkt TASKS, damit du Ausführung siehst.",
            "tasks-selected",
        ),
        (
            (70, 19),
            (55, 19),
            "content",
            FocusPane.CONTENT,
            "tasks",
            "Schritt 8: AI-Snake erklärt Task-Queue und laufende Worker.",
            "tasks-explain",
        ),
        (
            (68, 20),
            (57, 20),
            "detail",
            FocusPane.DETAIL,
            "tasks",
            "Schritt 9: Rechts erscheinen Detail-Hinweise für den aktiven Bereich.",
            "tasks-detail",
        ),
        (
            (66, 21),
            (62, 21),
            "follow",
            FocusPane.CONTENT,
            "tasks",
            "Schritt 10: Bei Kontakt mit deiner Position gibt die AI konkrete Tipps.",
            "contact",
        ),
        (
            (64, 22),
            (64, 22),
            "follow",
            FocusPane.CONTENT,
            "tasks",
            "Schritt 11: Beispiel-Tipp: :inspect ausführen und danach :refresh.",
            "contact-tip",
        ),
    ]
    history: list[dict[str, object]] = []
    for idx, (local_head, ai_head, target, focus, section_id, text, phase) in enumerate(nav_walk):
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
            status_message=f"snake walkthrough {idx + 1}/{len(nav_walk)} · run={run_id}",
            panel_states={section_id: PanelState.HEALTHY},
            section_payloads=payloads,
            header_logo_game=game,
        )
        screen = render_operator_shell(state, width=120, height=32)
        frames.append((base_time + idx * 0.7, "\u001b[2J\u001b[H" + screen + "\n"))
    return _asciinema_v2_lines(title="Ananta Operator TUI – Snake Mode Live", frames=frames)


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
