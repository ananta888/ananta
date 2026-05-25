from __future__ import annotations

import json
from pathlib import Path

from scripts.e2e.record_tui_demo import record_tui_demo

ROOT = Path(__file__).resolve().parents[2]


def _resolve_ref(ref: str) -> Path:
    ref_path = Path(ref)
    return ref_path if ref_path.is_absolute() else ROOT / ref_path


def test_tui_tutorial_ai_live_recording_contains_explainer_events(tmp_path: Path) -> None:
    payload = record_tui_demo(
        run_id="video-enable-tui-ai-live",
        flow_id="tui-tutorial-ai-live-video",
        enabled=True,
        scene="tutorial-ai-live",
        artifact_root=tmp_path / "artifacts",
    )

    assert payload["status"] == "recorded"
    video_path = _resolve_ref(payload["video_ref"])
    assert video_path.exists()
    assert video_path.name == "video-tui-tutorial-ai-live.cast"

    lines = [line for line in video_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    header = json.loads(lines[0])
    assert header["version"] == 2
    assert "Tutorial AI Snake Live" in header["title"]

    frames = [json.loads(line) for line in lines[1:]]
    assert len(frames) >= 4
    frame_text = "\n".join(str(frame[2]) for frame in frames if isinstance(frame, list) and len(frame) >= 3)
    assert "google/gemma-4-e4b" in frame_text
    assert "192.168.178.100:1234/v1" in frame_text
    assert "[target=header]" in frame_text
    assert "[target=nav]" in frame_text
    assert "[target=detail]" in frame_text
