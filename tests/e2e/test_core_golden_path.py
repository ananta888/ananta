from __future__ import annotations

import json
from pathlib import Path

from tests.e2e.harness import E2EHarness

ROOT = Path(__file__).resolve().parents[2]


def _resolve_ref(ref: str) -> Path:
    ref_path = Path(ref)
    return ref_path if ref_path.is_absolute() else ROOT / ref_path


def test_core_golden_path_creates_goal_task_artifact_trace_and_report(tmp_path: Path) -> None:
    harness = E2EHarness(artifact_root=tmp_path / "artifacts")
    result = harness.run_core_golden_path(goal="repair docker health", run_id="core-run-001")

    assert result.goal_id == "goal-repair-docker-health"
    assert result.task_id == "task-repair-docker-health"
    assert result.trace_id == "trace-task-repair-docker-health"
    assert result.flow_entry["status"] == "passed"
    assert result.flow_entry["artifact_refs"]
    assert result.flow_entry["trace_bundle_refs"] == [result.trace_id]

    artifact_file = _resolve_ref(result.flow_entry["artifact_refs"][0])
    assert artifact_file.exists()
    artifact_text = artifact_file.read_text(encoding="utf-8")
    assert "status=completed" in artifact_text
    assert "task_id=task-repair-docker-health" in artifact_text

    report_file = _resolve_ref(result.report_path)
    assert report_file.exists()
    report_payload = json.loads(report_file.read_text(encoding="utf-8"))
    assert report_payload["schema"] == "e2e_report.v1"
    assert report_payload["summary"]["total"] == 1
    assert report_payload["summary"]["passed"] == 1
    assert report_payload["flows"][0]["trace_bundle_refs"] == [result.trace_id]
    assert report_payload["flows"][0]["artifact_refs"] == result.flow_entry["artifact_refs"]
