from __future__ import annotations

import pytest

from scripts.validate_todo_consistency import validate_todo_payload


def _track_payload():
    return {"schema_version": "todo.v2", "tracks": [
        {"id": "track-a", "status": "partial", "priority": "P0", "tasks": [
            {"id": "a", "status": "partial", "depends_on": []},
        ]},
        {"id": "track-b", "status": "open", "priority": "P1", "tasks": [
            {"id": "b", "status": "open", "depends_on": ["a"]},
        ]},
    ]}


def test_nested_tracks_validate_all_tasks_and_cross_track_dependencies():
    assert validate_todo_payload(_track_payload()) == []


@pytest.mark.parametrize("mutation, expected", [
    ("duplicate_task", "unique nonempty id required across tracks"),
    ("duplicate_track", "unique nonempty id required"),
    ("unknown_dependency", "unknown task"),
    ("cycle", "dependency cycle"),
    ("self_dependency", "dependency cycle"),
    ("false_completion", "completed track contains unfinished tasks"),
    ("unknown_status", "unsupported status"),
    ("unknown_priority", "unsupported priority"),
    ("missing_tasks", "nonempty list required"),
    ("invalid_dependency", "string list required"),
])
def test_nested_track_inconsistencies_are_not_hidden(mutation, expected):
    value = _track_payload()
    first, second = value["tracks"]
    if mutation == "duplicate_task":
        second["tasks"][0]["id"] = "a"
    elif mutation == "duplicate_track":
        second["id"] = first["id"]
    elif mutation == "unknown_dependency":
        second["tasks"][0]["depends_on"] = ["unknown"]
    elif mutation == "cycle":
        first["tasks"][0]["depends_on"] = ["b"]
    elif mutation == "self_dependency":
        first["tasks"][0]["depends_on"] = ["a"]
    elif mutation == "false_completion":
        first["status"] = "completed"
    elif mutation == "unknown_status":
        first["tasks"][0]["status"] = "invented"
    elif mutation == "unknown_priority":
        first["tasks"][0]["priority"] = "P999"
    elif mutation == "missing_tasks":
        first["tasks"] = []
    else:
        second["tasks"][0]["depends_on"] = [True]
    assert any(expected in problem for problem in validate_todo_payload(value))


def _build_payload() -> dict:
    return {
        "tasks": [
            {"id": "A-1", "status": "done", "priority": "P0", "risk": "critical"},
            {"id": "A-2", "status": "todo", "priority": "P1", "risk": "high"},
        ],
        "priority_scale": ["P0", "P1", "P2", "P3"],
        "risk_scale": ["low", "medium", "high", "critical"],
        "critical_path_tasks": ["A-1"],
        "milestones": [{"id": "M1", "status": "in_progress"}],
        "tasks_status_summary": {
            "total": 2,
            "by_status": {"todo": 1, "in_progress": 0, "partial": 0, "blocked": 0, "done": 1},
            "progress_percent_done": 50.0,
            "by_priority": {"P0": 1, "P1": 1, "P2": 0, "P3": 0},
            "by_risk": {"low": 0, "medium": 0, "high": 1, "critical": 1},
            "critical_path": {"total": 1, "done": 1, "remaining": 0},
            "milestones": {
                "total": 1,
                "todo": 0,
                "in_progress": 1,
                "partial": 0,
                "blocked": 0,
                "done": 0,
            },
        },
        "execution_stage_summary": {
            "stages": {
                "M1": {
                    "scope_task_ids": ["A-1", "A-2"],
                    "total": 2,
                    "done": 1,
                    "todo": 1,
                    "in_progress": 0,
                    "partial": 0,
                    "blocked": 0,
                }
            }
        },
    }


def test_validate_todo_payload_returns_no_problems_when_consistent() -> None:
    assert validate_todo_payload(_build_payload()) == []


def _nested_payload():
    payload = _build_payload()
    for group, field in (("by_priority", "priority"), ("by_risk", "risk")):
        for key in payload["tasks_status_summary"][group]:
            payload["tasks_status_summary"][group][key] = {
                status: sum(task[field] == key and task["status"] == status for task in payload["tasks"])
                for status in ("todo", "in_progress", "partial", "blocked", "done")
            }
    return payload


def test_canonical_nested_priority_and_risk_counts_are_supported():
    assert validate_todo_payload(_nested_payload()) == []


@pytest.mark.parametrize("group,key", [("by_priority", "P0"), ("by_risk", "critical")])
def test_nested_counter_status_drift_is_detected_even_with_same_total(group, key):
    payload = _nested_payload()
    payload["tasks_status_summary"][group][key].update(done=0, todo=1)
    problems = validate_todo_payload(payload)
    assert any(problem.startswith(f"{group}.{key}.done:") for problem in problems)


@pytest.mark.parametrize("count", [True, 1.0, -1, "1"])
def test_nested_counts_require_nonnegative_integers(count):
    payload = _nested_payload()
    payload["tasks_status_summary"]["by_priority"]["P0"]["done"] = count
    assert any("nonnegative integer required" in problem for problem in validate_todo_payload(payload))


def test_nested_counter_unknown_status_is_rejected():
    payload = _nested_payload()
    payload["tasks_status_summary"]["by_priority"]["P0"]["invented"] = 1
    assert any("unknown status counters" in problem for problem in validate_todo_payload(payload))


def test_validate_todo_payload_uses_canonical_one_decimal_progress() -> None:
    payload = _build_payload()
    payload["tasks"].append(
        {
            "id": "A-3",
            "status": "done",
            "priority": "P0",
            "risk": "critical",
        }
    )
    summary = payload["tasks_status_summary"]
    summary["total"] = 3
    summary["by_status"]["done"] = 2
    summary["progress_percent_done"] = 66.7
    summary["by_priority"]["P0"] = 2
    summary["by_risk"]["critical"] = 2
    stage = payload["execution_stage_summary"]["stages"]["M1"]
    stage["scope_task_ids"].append("A-3")
    stage["total"] = 3
    stage["done"] = 2

    assert validate_todo_payload(payload) == []


def test_validate_todo_payload_reports_summary_and_stage_drift() -> None:
    payload = _build_payload()
    payload["tasks_status_summary"]["by_status"]["done"] = 0
    payload["execution_stage_summary"]["stages"]["M1"]["done"] = 0

    problems = validate_todo_payload(payload)

    assert any(problem.startswith("by_status.done:") for problem in problems)
    assert any(problem.startswith("M1.done:") for problem in problems)


def test_validate_todo_payload_rejects_partial_counter_drift_everywhere() -> None:
    payload = _build_payload()
    payload["tasks"][1]["status"] = "partial"
    payload["milestones"][0]["status"] = "partial"
    payload["progress_summary"] = {
        "todo_remaining": 0,
        "in_progress": 0,
        "partial": 0,
        "blocked": 0,
        "done": 1,
        "milestones_done": 0,
        "milestones_total": 1,
    }
    payload["execution_stage_summary"]["stages"]["M1"]["todo"] = 0

    problems = validate_todo_payload(payload)

    assert any(problem.startswith("by_status.partial:") for problem in problems)
    assert any(problem.startswith("milestones.partial:") for problem in problems)
    assert any(problem.startswith("progress_summary.partial:") for problem in problems)
    assert any(problem.startswith("M1.partial:") for problem in problems)


def test_validate_category_todo_payload_accepts_consistent_summary() -> None:
    payload = {
        "categories": [
            {
                "name": "ops",
                "label": "Operations",
                "items": [
                    {
                        "id": "OPS-1",
                        "status": "completed",
                        "depends_on": [],
                    },
                    {
                        "id": "OPS-2",
                        "status": "partial",
                        "depends_on": ["OPS-1"],
                    },
                ],
            }
        ],
        "meta": {
            "total_items": 2,
            "by_status": {
                "completed": 1,
                "partial": 1,
                "open": 0,
            },
            "recommended_order": ["OPS-1", "OPS-2"],
        },
    }

    assert validate_todo_payload(payload) == []


def test_validate_category_todo_payload_reports_drift_and_unknown_refs() -> None:
    payload = {
        "categories": [
            {
                "name": "ops",
                "label": "Operations",
                "items": [
                    {
                        "id": "OPS-1",
                        "status": "open",
                        "depends_on": ["MISSING"],
                    }
                ],
            }
        ],
        "meta": {
            "total_items": 2,
            "by_status": {
                "completed": 0,
                "partial": 0,
                "open": 0,
            },
            "recommended_order": ["MISSING"],
        },
    }

    problems = validate_todo_payload(payload)

    assert any(problem.startswith("meta.total_items:") for problem in problems)
    assert any(problem.startswith("meta.by_status.open:") for problem in problems)
    assert any("depends_on references unknown ids" in problem for problem in problems)
    assert any("recommended_order references unknown ids" in problem for problem in problems)
