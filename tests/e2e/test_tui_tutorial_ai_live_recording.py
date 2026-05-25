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

    cast = json.loads(video_path.read_text(encoding="utf-8"))
    assert cast["flow"] == "tui_tutorial_ai_live"
    assert cast["metadata"]["snake_id"] == "s-ai"
    assert cast["metadata"]["model"] == "google/gemma-4-e4b"
    assert cast["metadata"]["openai_api_base_url"] == "http://192.168.178.100:1234/v1"
    events = list(cast.get("events") or [])
    assert len(events) >= 4
    assert any(event.get("target") == "header" for event in events)
    assert any(event.get("target") == "nav" for event in events)
    assert any(event.get("target") == "detail" for event in events)
