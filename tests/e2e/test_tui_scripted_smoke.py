from __future__ import annotations

from pathlib import Path

from scripts.e2e.capture_tui import capture_tui_screens
from scripts.e2e.render_terminal_snapshot import render_terminal_snapshot
from tests.e2e.harness import E2EHarness

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots" / "tui"


def _resolve_ref(ref: str) -> Path:
    ref_path = Path(ref)
    return ref_path if ref_path.is_absolute() else ROOT / ref_path


def _expected_snapshot(name: str) -> str:
    return (SNAPSHOT_DIR / f"{name}.txt").read_text(encoding="utf-8")


def test_tui_scripted_smoke_captures_required_screens_and_evidence(tmp_path: Path) -> None:
    harness = E2EHarness(artifact_root=tmp_path / "artifacts")
    result = harness.run_tui_scripted_smoke(run_id="tui-smoke-001")

    assert set(result.snapshot_refs.keys()) == {"health", "task_list", "artifact_view", "degraded"}
    assert set(result.screenshot_refs.keys()) == {"health", "task_list", "artifact_view", "degraded"}

    for name, ref in result.snapshot_refs.items():
        snapshot_text = _resolve_ref(ref).read_text(encoding="utf-8")
        assert harness.normalize_terminal_snapshot(snapshot_text) == _expected_snapshot(name)
        assert snapshot_text.strip()

    for ref in result.screenshot_refs.values():
        screenshot_path = _resolve_ref(ref)
        assert screenshot_path.exists()
        assert screenshot_path.suffix == ".png"
        assert screenshot_path.read_bytes()

    assert result.report["summary"]["passed"] == 1
    assert result.report["summary"]["blocking_failed"] == 0


def test_render_terminal_snapshot_normalizes_whitespace_and_sensitive_data() -> None:
    raw = "token=abcdef1234567890SECRET\npath=/home/user/private/data\nok\tline\n"
    rendered = render_terminal_snapshot(raw, width=120)
    assert "token=<REDACTED>" in rendered
    assert "<REDACTED_PATH>" in rendered
    assert "ok    line" in rendered


def test_capture_tui_script_helper_outputs_expected_structure(tmp_path: Path) -> None:
    payload = capture_tui_screens(run_id="tui-capture-001", artifact_root=tmp_path / "artifacts")
    assert payload["run_id"] == "tui-capture-001"
    assert payload["flow_id"] == "tui-scripted-smoke"
    assert set(payload["snapshots"].keys()) == {"health", "task_list", "artifact_view", "degraded"}
    assert set(payload["screenshots"].keys()) == {"health", "task_list", "artifact_view", "degraded"}
