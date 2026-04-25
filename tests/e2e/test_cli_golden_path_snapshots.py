from __future__ import annotations

from pathlib import Path

from tests.e2e.harness import E2EHarness

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots" / "cli"


def _resolve_ref(ref: str) -> Path:
    ref_path = Path(ref)
    return ref_path if ref_path.is_absolute() else ROOT / ref_path


def _expected_snapshot(name: str) -> str:
    return (SNAPSHOT_DIR / f"{name}.txt").read_text(encoding="utf-8")


def test_cli_golden_path_snapshots_match_baselines(tmp_path: Path) -> None:
    harness = E2EHarness(artifact_root=tmp_path / "artifacts")
    result = harness.run_cli_golden_path(goal="repair docker health", run_id="cli-golden-001")

    assert set(result.snapshot_refs.keys()) == {"health", "goal_submit", "task_status", "artifact_show"}
    for snapshot_name, snapshot_ref in result.snapshot_refs.items():
        snapshot_text = _resolve_ref(snapshot_ref).read_text(encoding="utf-8")
        normalized = harness.normalize_cli_snapshot(snapshot_text)
        assert normalized == _expected_snapshot(snapshot_name)

    goal_snapshot = _resolve_ref(result.snapshot_refs["goal_submit"]).read_text(encoding="utf-8")
    assert "next step:" in goal_snapshot.lower()
    assert result.report["summary"]["passed"] == 1
    assert result.report["summary"]["blocking_failed"] == 0
