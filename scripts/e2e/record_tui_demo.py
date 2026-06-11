from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.e2e.e2e_artifacts import write_text_artifact
from scripts.e2e.tui_demo_playback import (
    _click_select_explain_cast,
    _share_session_e2e_cast,
    _snake_lmstudio_heuristic_cast,
    _snake_mode_live_cast,
    _sync_snake_mode_live_cast_targets,
    _sync_tutorial_ai_live_cast_targets,
    _tutorial_ai_live_cast,
)
from scripts.e2e.tui_demo_recorder import (
    _asciinema_v2_lines,
    _share_session_live_e2e_cast,
    _snake_mode_live_e2e_cast,
)


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
            title="Ananta Operator TUI \u2013 Demo Placeholder",
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
