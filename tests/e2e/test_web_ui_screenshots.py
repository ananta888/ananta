from __future__ import annotations

from pathlib import Path

from scripts.e2e.capture_web_ui import capture_web_ui_screens
from tests.e2e.harness import E2EHarness

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots" / "web"


def _resolve_ref(ref: str) -> Path:
    ref_path = Path(ref)
    return ref_path if ref_path.is_absolute() else ROOT / ref_path


def _expected_snapshot(name: str) -> str:
    return (SNAPSHOT_DIR / f"{name}.txt").read_text(encoding="utf-8")


def test_web_ui_screenshots_match_baselines_when_available(tmp_path: Path) -> None:
    harness = E2EHarness(artifact_root=tmp_path / "artifacts")
    result = harness.run_web_ui_screenshots(run_id="web-shot-001", web_available=True)

    assert set(result.snapshot_refs.keys()) == {"dashboard", "goals_tasks", "artifact_view", "degraded"}
    assert set(result.screenshot_refs.keys()) == {"dashboard", "goals_tasks", "artifact_view", "degraded"}

    for name, ref in result.snapshot_refs.items():
        snapshot_text = _resolve_ref(ref).read_text(encoding="utf-8")
        assert harness.normalize_web_snapshot(snapshot_text) == _expected_snapshot(name)

    for ref in result.screenshot_refs.values():
        screenshot_path = _resolve_ref(ref)
        assert screenshot_path.exists()
        assert screenshot_path.suffix == ".png"
        assert screenshot_path.read_bytes()

    assert result.report["summary"]["passed"] == 1
    assert result.flow_entry["blocking"] is False


def test_web_ui_screenshots_are_advisory_when_unavailable(tmp_path: Path) -> None:
    harness = E2EHarness(artifact_root=tmp_path / "artifacts")
    result = harness.run_web_ui_screenshots(run_id="web-shot-002", web_available=False)

    assert result.flow_entry["status"] == "advisory"
    assert result.flow_entry["blocking"] is False
    assert "web_unavailable" in result.snapshot_refs


def test_capture_web_ui_script_helper_handles_available_and_unavailable(tmp_path: Path) -> None:
    available = capture_web_ui_screens(
        run_id="web-capture-001",
        artifact_root=tmp_path / "artifacts",
        web_available=True,
    )
    unavailable = capture_web_ui_screens(
        run_id="web-capture-002",
        artifact_root=tmp_path / "artifacts",
        web_available=False,
    )
    assert available["status"] == "passed"
    assert set(available["snapshots"].keys()) == {"dashboard", "goals_tasks", "artifact_view", "degraded"}
    assert unavailable["status"] == "advisory"
    assert "web_unavailable" in unavailable["snapshots"]
