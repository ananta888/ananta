from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.e2e.record_tui_demo import record_tui_demo

ROOT = Path(__file__).resolve().parents[2]


def _resolve_ref(ref: str) -> Path:
    p = Path(ref)
    return p if p.is_absolute() else ROOT / p


def test_cast_contains_mouse_follow_marker() -> None:
    payload = record_tui_demo(
        run_id="video-enable-snake-mode-mouse-cast",
        flow_id="tui-mouse-snake-artifact-chat",
        enabled=True,
        scene="snake-mode-live",
    )
    video_path = _resolve_ref(str(payload["video_ref"]))
    lines = [line for line in video_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    frame_text = "\n".join(json.loads(line)[2] for line in lines[1:])
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", frame_text)
    assert "[mouse-follow]" in plain


def test_cast_contains_ai_fast_target_marker() -> None:
    payload = record_tui_demo(
        run_id="video-enable-snake-mode-mouse-cast-2",
        flow_id="tui-mouse-snake-artifact-chat",
        enabled=True,
        scene="snake-mode-live",
    )
    video_path = _resolve_ref(str(payload["video_ref"]))
    lines = [line for line in video_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    frame_text = "\n".join(json.loads(line)[2] for line in lines[1:])
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", frame_text)
    assert "[ai-fast-target]" in plain


def test_cast_contains_artifact_chat_marker() -> None:
    payload = record_tui_demo(
        run_id="video-enable-snake-mode-mouse-cast-3",
        flow_id="tui-mouse-snake-artifact-chat",
        enabled=True,
        scene="snake-mode-live",
    )
    video_path = _resolve_ref(str(payload["video_ref"]))
    lines = [line for line in video_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    frame_text = "\n".join(json.loads(line)[2] for line in lines[1:])
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", frame_text)
    assert "[artifact-chat-active]" in plain
