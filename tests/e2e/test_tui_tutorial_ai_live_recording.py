from __future__ import annotations

import json
from pathlib import Path

from scripts.e2e.record_tui_demo import record_tui_demo

ROOT = Path(__file__).resolve().parents[2]


def _resolve_ref(ref: str) -> Path:
    ref_path = Path(ref)
    return ref_path if ref_path.is_absolute() else ROOT / ref_path


def test_tui_tutorial_ai_live_recording_contains_explainer_events(tmp_path: Path) -> None:
    synced_a = tmp_path / "synced" / "tests-output.cast"
    synced_b = tmp_path / "synced" / "web-asset.cast"
    payload = record_tui_demo(
        run_id="video-enable-tui-ai-live",
        flow_id="tui-tutorial-ai-live-video",
        enabled=True,
        scene="tutorial-ai-live",
        sync_targets=[synced_a, synced_b],
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
    synced_targets = list(payload.get("synced_cast_targets") or [])
    assert len(synced_targets) == 2
    assert synced_a.exists()
    assert synced_b.exists()
    assert synced_a.read_text(encoding="utf-8") == synced_b.read_text(encoding="utf-8")


def test_tui_tutorial_ai_live_recording_default_sync_targets_are_written(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    payload = record_tui_demo(
        run_id="video-enable-tui-ai-live-default-sync",
        flow_id="tui-tutorial-ai-live-video",
        enabled=True,
        scene="tutorial-ai-live",
        artifact_root=tmp_path / "artifacts",
    )

    synced_targets = list(payload.get("synced_cast_targets") or [])
    assert len(synced_targets) == 4
    target_paths = [Path(path) for path in synced_targets]
    for target in target_paths:
        assert target.exists()
    assert target_paths[0].as_posix().endswith("tests/output/operator_tui_splash.cast")
    assert target_paths[1].as_posix().endswith("tests/output/operator_tui_tutorial_ai_live.cast")
    assert target_paths[2].as_posix().endswith("web/www/assets/operator_tui_tutorial_ai_live.cast")
    assert target_paths[3].as_posix().endswith("web/www/assets/operator_tui_splash.cast")
