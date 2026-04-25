from __future__ import annotations

from pathlib import Path

from scripts.e2e.record_tui_demo import record_tui_demo
from scripts.e2e.record_web_demo import record_web_demo

ROOT = Path(__file__).resolve().parents[2]


def _resolve_ref(ref: str) -> Path:
    ref_path = Path(ref)
    return ref_path if ref_path.is_absolute() else ROOT / ref_path


def test_optional_video_capture_is_skipped_by_default(tmp_path: Path) -> None:
    tui = record_tui_demo(run_id="video-skip-tui", artifact_root=tmp_path / "artifacts", enabled=False)
    web = record_web_demo(run_id="video-skip-web", artifact_root=tmp_path / "artifacts", enabled=False)
    assert tui["status"] == "skipped"
    assert web["status"] == "skipped"
    assert tui["optional"] is True
    assert web["optional"] is True


def test_optional_video_capture_can_be_enabled(tmp_path: Path) -> None:
    tui = record_tui_demo(run_id="video-enable-tui", artifact_root=tmp_path / "artifacts", enabled=True)
    web = record_web_demo(run_id="video-enable-web", artifact_root=tmp_path / "artifacts", enabled=True)

    assert tui["status"] == "recorded"
    assert web["status"] == "recorded"
    assert _resolve_ref(tui["video_ref"]).exists()
    assert _resolve_ref(web["video_ref"]).exists()
