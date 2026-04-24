from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate todo summary and stage counters.")
    parser.add_argument("--todo", default="todo.json", help="Path to todo JSON file.")
    return parser.parse_args()


def _expect_equal(problems: list[str], label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        problems.append(f"{label}: expected={expected!r} actual={actual!r}")


def validate_todo_payload(todo_payload: dict[str, Any]) -> list[str]:
    tasks = list(todo_payload.get("tasks") or [])
    summary = dict(todo_payload.get("tasks_status_summary") or {})
    by_status = dict(summary.get("by_status") or {})
    by_priority = dict(summary.get("by_priority") or {})
    by_risk = dict(summary.get("by_risk") or {})
    critical_path = dict(summary.get("critical_path") or {})
    milestone_summary = dict(summary.get("milestones") or {})
    milestones = list(todo_payload.get("milestones") or [])
    priority_scale = list(todo_payload.get("priority_scale") or [])
    risk_scale = list(todo_payload.get("risk_scale") or [])
    execution_stages = dict((todo_payload.get("execution_stage_summary") or {}).get("stages") or {})

    problems: list[str] = []

    _expect_equal(problems, "tasks_status_summary.total", summary.get("total"), len(tasks))

    status_counts = Counter(str(task.get("status")) for task in tasks if task.get("status"))
    for status_key in ("todo", "in_progress", "blocked", "done"):
        _expect_equal(
            problems, f"by_status.{status_key}", by_status.get(status_key, 0), status_counts.get(status_key, 0)
        )

    priority_counts = Counter(str(task.get("priority")) for task in tasks if task.get("priority"))
    for priority_key in priority_scale:
        _expect_equal(
            problems,
            f"by_priority.{priority_key}",
            by_priority.get(priority_key, 0),
            priority_counts.get(priority_key, 0),
        )

    risk_counts = Counter(str(task.get("risk", "medium")) for task in tasks)
    for risk_key in risk_scale:
        _expect_equal(
            problems,
            f"by_risk.{risk_key}",
            by_risk.get(risk_key, 0),
            risk_counts.get(risk_key, 0),
        )

    done_count = status_counts.get("done", 0)
    expected_progress = round((done_count / len(tasks)) * 100, 2) if tasks else 0.0
    actual_progress = float(summary.get("progress_percent_done", 0.0))
    if not math.isclose(actual_progress, expected_progress, rel_tol=0.0, abs_tol=0.01):
        problems.append(f"progress_percent_done: expected={expected_progress!r} actual={actual_progress!r}")

    critical_ids = set(todo_payload.get("critical_path_tasks") or [])
    critical_done = sum(1 for task in tasks if task.get("id") in critical_ids and task.get("status") == "done")
    _expect_equal(problems, "critical_path.total", critical_path.get("total"), len(critical_ids))
    _expect_equal(problems, "critical_path.done", critical_path.get("done"), critical_done)
    _expect_equal(
        problems, "critical_path.remaining", critical_path.get("remaining"), len(critical_ids) - critical_done
    )

    milestone_status_counts = Counter(
        str(milestone.get("status")) for milestone in milestones if milestone.get("status")
    )
    _expect_equal(problems, "milestones.total", milestone_summary.get("total"), len(milestones))
    for status_key in ("todo", "in_progress", "blocked", "done"):
        _expect_equal(
            problems,
            f"milestones.{status_key}",
            milestone_summary.get(status_key, 0),
            milestone_status_counts.get(status_key, 0),
        )

    status_by_task_id = {str(task.get("id")): str(task.get("status")) for task in tasks if task.get("id")}
    for stage_name, stage in execution_stages.items():
        scope_task_ids = [str(task_id) for task_id in list(stage.get("scope_task_ids") or [])]
        counts = Counter(status_by_task_id.get(task_id, "missing_task") for task_id in scope_task_ids)
        _expect_equal(problems, f"{stage_name}.total", stage.get("total"), len(scope_task_ids))
        _expect_equal(problems, f"{stage_name}.done", stage.get("done"), counts.get("done", 0))
        _expect_equal(problems, f"{stage_name}.todo", stage.get("todo"), counts.get("todo", 0))
        _expect_equal(problems, f"{stage_name}.in_progress", stage.get("in_progress"), counts.get("in_progress", 0))
        _expect_equal(problems, f"{stage_name}.blocked", stage.get("blocked"), counts.get("blocked", 0))

    return problems


def main() -> int:
    args = _parse_args()
    todo_path = Path(args.todo).resolve()
    todo_payload = json.loads(todo_path.read_text(encoding="utf-8"))
    problems = validate_todo_payload(todo_payload)
    if problems:
        print("todo-consistency-invalid")
        for problem in problems:
            print(f"- {problem}")
        return 2
    print("todo-consistency-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
