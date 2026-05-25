from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

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


def record_tui_demo(
    *,
    run_id: str,
    flow_id: str = "tui-demo-video",
    enabled: bool = False,
    scene: str = "default",
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    if not enabled:
        return {
            "status": "skipped",
            "optional": True,
            "reason": "video capture disabled (set --enable to record)",
            "video_ref": "",
        }
    if str(scene).strip().lower() == "tutorial-ai-live":
        cast_content = _tutorial_ai_live_cast(run_id=run_id)
        file_name = "video-tui-tutorial-ai-live.cast"
    else:
        cast_content = _asciinema_v2_lines(
            title="Ananta Operator TUI – Demo Placeholder",
            frames=[(0.0, "\u001b[2J\u001b[Hananta tui demo placeholder\n")],
        )
        file_name = "video-tui-demo.cast"
    video_ref = write_text_artifact(
        run_id,
        flow_id,
        file_name,
        cast_content,
        artifact_root=artifact_root,
    )
    return {"status": "recorded", "optional": True, "reason": "", "video_ref": video_ref}


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
