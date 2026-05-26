from __future__ import annotations

import json
from pathlib import Path

from agent.services.planning_summary_engine import PlanningSummaryEngine


def _fixture_payload() -> dict:
    fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "planning_tracks" / "small_track.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


def test_summary_engine_computes_deterministic_source_hash() -> None:
    payload = _fixture_payload()
    engine = PlanningSummaryEngine()
    first, _ = engine.recompute(payload)
    second, _ = engine.recompute(payload)
    assert first["derived_summary_metadata"]["source_hash"] == second["derived_summary_metadata"]["source_hash"]
    assert first["weighted_progress_summary"] == second["weighted_progress_summary"]
    assert first["tasks_status_summary"] == second["tasks_status_summary"]


def test_summary_engine_normalizes_progress_semantics_and_reports_mismatches() -> None:
    payload = _fixture_payload()
    payload["tasks"][0]["status"] = "done"
    payload["tasks"][0]["progress_percent"] = 55
    payload["tasks"][1]["status"] = "todo"
    payload["tasks"][1]["progress_percent"] = 10
    payload["tasks"][2]["status"] = "in_progress"
    payload["tasks"][2]["progress_percent"] = 100

    computed, issues = PlanningSummaryEngine().recompute(payload)

    assert computed["tasks"][0]["progress_percent"] == 100.0
    assert computed["tasks"][1]["progress_percent"] == 0.0
    assert computed["tasks"][2]["progress_percent"] == 99.0
    assert any(item["reason_code"] == "progress_status_mismatch" for item in issues)


def test_summary_engine_weighted_progress_prioritizes_critical_high_risk() -> None:
    payload = _fixture_payload()
    payload["tasks"] = [
        {
            "id": "T1",
            "title": "Critical done",
            "status": "done",
            "priority": "P1",
            "risk": "high",
            "type": "backend",
            "acceptance_criteria": ["ok"],
        },
        {
            "id": "T2",
            "title": "Non critical todo",
            "status": "todo",
            "priority": "P3",
            "risk": "low",
            "type": "docs",
            "acceptance_criteria": ["ok"],
        },
    ]
    payload["critical_path_tasks"] = ["T1"]
    payload["milestones"] = [{"id": "M1", "title": "M1", "task_ids": ["T1", "T2"], "status": "in_progress"}]

    computed, _ = PlanningSummaryEngine().recompute(payload)
    assert computed["progress_summary"]["count_based_percent"] == 50.0
    assert computed["weighted_progress_summary"]["weighted_percent"] > 50.0


def test_summary_engine_derives_milestone_status_and_progress() -> None:
    payload = _fixture_payload()
    payload["tasks"] = [
        {
            "id": "T1",
            "title": "Done task",
            "status": "done",
            "priority": "P1",
            "risk": "high",
            "type": "backend",
            "acceptance_criteria": ["ok"],
        },
        {
            "id": "T2",
            "title": "In progress task",
            "status": "in_progress",
            "priority": "P2",
            "risk": "medium",
            "type": "test",
            "acceptance_criteria": ["ok"],
            "progress_percent": 40,
        },
    ]
    payload["milestones"] = [{"id": "M1", "title": "M1", "task_ids": ["T1", "T2"], "status": "todo"}]
    computed, _ = PlanningSummaryEngine().recompute(payload)
    assert computed["milestones"][0]["status"] == "in_progress"
    assert computed["milestone_progress_summary"]["milestones"]["M1"]["status"] == "in_progress"
    assert computed["milestone_progress_summary"]["milestones"]["M1"]["total_tasks"] == 2
