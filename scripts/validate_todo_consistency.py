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


def _validate_group_counts(problems, label, actual, expected_statuses, status_scale):
    """Support legacy totals and canonical status maps without hiding drift."""
    if isinstance(actual, dict):
        unknown = set(actual) - set(status_scale)
        if unknown:
            problems.append(f"{label}: unknown status counters {sorted(unknown)!r}")
        for status in status_scale:
            count = actual.get(status, 0)
            if type(count) is not int or count < 0:
                problems.append(f"{label}.{status}: nonnegative integer required")
            else:
                _expect_equal(problems, f"{label}.{status}", count, expected_statuses.get(status, 0))
    elif type(actual) is not int or actual < 0:
        problems.append(f"{label}: nonnegative integer or status map required")
    else:
        _expect_equal(problems, label, actual, sum(expected_statuses.values()))


def _validate_category_todo_payload(
    todo_payload: dict[str, Any],
) -> list[str]:
    """Validate the summary cache and references of todo.schema.json files."""

    categories = list(todo_payload.get("categories") or [])
    items = [
        item
        for category in categories
        if isinstance(category, dict)
        for item in list(category.get("items") or [])
        if isinstance(item, dict)
    ]
    meta = dict(todo_payload.get("meta") or {})
    summary = dict(meta.get("by_status") or {})
    problems: list[str] = []

    _expect_equal(
        problems,
        "meta.total_items",
        meta.get("total_items"),
        len(items),
    )
    status_counts = Counter(
        str(item.get("status"))
        for item in items
        if item.get("status")
    )
    for status in ("completed", "partial", "open"):
        _expect_equal(
            problems,
            f"meta.by_status.{status}",
            summary.get(status),
            status_counts.get(status, 0),
        )
    unknown_statuses = sorted(
        status
        for status in status_counts
        if status not in {"completed", "partial", "open"}
    )
    if unknown_statuses:
        problems.append(
            "category item statuses are unsupported: "
            + ", ".join(unknown_statuses)
        )

    item_ids = [str(item.get("id") or "") for item in items]
    missing_ids = sum(not item_id for item_id in item_ids)
    if missing_ids:
        problems.append(f"category items missing id: {missing_ids}")
    known_ids = {item_id for item_id in item_ids if item_id}
    if len(known_ids) != len(item_ids) - missing_ids:
        problems.append("category item ids must be unique")

    for item in items:
        item_id = str(item.get("id") or "<missing>")
        dependencies = list(item.get("depends_on") or [])
        unknown_dependencies = sorted(
            str(dependency)
            for dependency in dependencies
            if str(dependency) not in known_ids
        )
        if unknown_dependencies:
            problems.append(
                f"{item_id}.depends_on references unknown ids: "
                + ", ".join(unknown_dependencies)
            )

    recommended_order = [
        str(item_id)
        for item_id in list(meta.get("recommended_order") or [])
    ]
    unknown_recommendations = sorted(
        item_id
        for item_id in recommended_order
        if item_id not in known_ids
    )
    if unknown_recommendations:
        problems.append(
            "meta.recommended_order references unknown ids: "
            + ", ".join(unknown_recommendations)
        )
    if len(set(recommended_order)) != len(recommended_order):
        problems.append("meta.recommended_order contains duplicates")
    return problems


def _validate_nested_tracks(todo_payload: dict[str, Any]) -> list[str]:
    """Validate todo.v2 tracks without pretending that nested tasks are absent."""
    tracks = todo_payload.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        return ["tracks: nonempty list required"]
    problems = []
    tasks = []
    track_ids = set()
    statuses = {"open", "todo", "in_progress", "partial", "blocked", "done", "completed"}
    priorities = set(todo_payload.get("priority_scale") or ("P0", "P1", "P2", "P3"))
    for track in tracks:
        if not isinstance(track, dict):
            problems.append("tracks: object required")
            continue
        identifier = track.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in track_ids:
            problems.append("tracks: unique nonempty id required")
        else:
            track_ids.add(identifier)
        if track.get("status") not in statuses:
            problems.append(f"{identifier}.status: unsupported status")
        entries = track.get("tasks")
        if not isinstance(entries, list) or not entries:
            problems.append(f"{identifier}.tasks: nonempty list required")
            continue
        for task in entries:
            if not isinstance(task, dict):
                problems.append(f"{identifier}.tasks: object required")
                continue
            tasks.append(task)
            if task.get("status") not in statuses:
                problems.append(f"{task.get('id')}.status: unsupported status")
            if task.get("priority", track.get("priority")) not in priorities:
                problems.append(f"{task.get('id')}.priority: unsupported priority")
        if track.get("status") in {"done", "completed"} and any(
            not isinstance(task, dict) or task.get("status") not in {"done", "completed"} for task in entries
        ):
            problems.append(f"{identifier}.status: completed track contains unfinished tasks")
    known = set()
    graph = {}
    for task in tasks:
        identifier = task.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in known:
            problems.append("tasks: unique nonempty id required across tracks")
            continue
        known.add(identifier)
        dependencies = task.get("depends_on", [])
        if not isinstance(dependencies, list) or any(not isinstance(value, str) for value in dependencies):
            problems.append(f"{identifier}.depends_on: string list required")
            graph[identifier] = set()
        else:
            graph[identifier] = set(dependencies)
            if len(dependencies) != len(graph[identifier]):
                problems.append(f"{identifier}.depends_on: duplicate dependency")
    for identifier, dependencies in graph.items():
        for dependency in sorted(dependencies - known):
            problems.append(f"{identifier}.depends_on: unknown task {dependency}")
    # Iterative cycle detection also handles self-dependencies without recursion limits.
    pending = {identifier: dependencies & known for identifier, dependencies in graph.items()}
    while pending:
        ready = {identifier for identifier, dependencies in pending.items() if not dependencies}
        if not ready:
            problems.append(f"tasks.depends_on: dependency cycle among {sorted(pending)}")
            break
        pending = {identifier: dependencies - ready for identifier, dependencies in pending.items()
                   if identifier not in ready}
    return problems


def validate_todo_payload(todo_payload: dict[str, Any]) -> list[str]:
    if todo_payload.get("schema_version") == "todo.v2" and "tracks" in todo_payload:
        return _validate_nested_tracks(todo_payload)
    if "categories" in todo_payload and "tasks" not in todo_payload:
        return _validate_category_todo_payload(todo_payload)

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
    status_scale = list(
        todo_payload.get("status_scale") or ("todo", "in_progress", "partial", "blocked", "done")
    )
    progress_summary = dict(todo_payload.get("progress_summary") or {})
    execution_stages = dict((todo_payload.get("execution_stage_summary") or {}).get("stages") or {})

    problems: list[str] = []

    _expect_equal(problems, "tasks_status_summary.total", summary.get("total"), len(tasks))

    status_counts = Counter(str(task.get("status")) for task in tasks if task.get("status"))
    for status_key in status_scale:
        _expect_equal(
            problems, f"by_status.{status_key}", by_status.get(status_key, 0), status_counts.get(status_key, 0)
        )
    unknown_status_count = sum(count for key, count in status_counts.items() if key not in status_scale)
    _expect_equal(problems, "by_status.unknown", unknown_status_count, 0)

    for priority_key in priority_scale:
        _validate_group_counts(
            problems,
            f"by_priority.{priority_key}",
            by_priority.get(priority_key, 0),
            Counter(str(task.get("status")) for task in tasks if str(task.get("priority")) == priority_key),
            status_scale,
        )

    for risk_key in risk_scale:
        _validate_group_counts(
            problems,
            f"by_risk.{risk_key}",
            by_risk.get(risk_key, 0),
            Counter(str(task.get("status")) for task in tasks if str(task.get("risk", "medium")) == risk_key),
            status_scale,
        )

    done_count = status_counts.get("done", 0)
    # Keep this legacy validator aligned with the canonical planning summary
    # engine, which renders count-based percentages to one decimal place.
    expected_progress = round((done_count / len(tasks)) * 100, 1) if tasks else 0.0
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
    for status_key in status_scale:
        _expect_equal(
            problems,
            f"milestones.{status_key}",
            milestone_summary.get(status_key, 0),
            milestone_status_counts.get(status_key, 0),
        )

    if progress_summary:
        todo_status = "todo" if "todo" in status_scale else "open"
        progress_fields = {
            "todo_remaining": status_counts.get(todo_status, 0),
            "in_progress": status_counts.get("in_progress", 0),
            "partial": status_counts.get("partial", 0),
            "blocked": status_counts.get("blocked", 0),
            "done": status_counts.get("done", 0),
            "milestones_done": milestone_status_counts.get("done", 0),
            "milestones_total": len(milestones),
        }
        for field, expected in progress_fields.items():
            if field in progress_summary:
                _expect_equal(problems, f"progress_summary.{field}", progress_summary.get(field), expected)

    status_by_task_id = {str(task.get("id")): str(task.get("status")) for task in tasks if task.get("id")}
    for stage_name, stage in execution_stages.items():
        scope_task_ids = [str(task_id) for task_id in list(stage.get("scope_task_ids") or [])]
        counts = Counter(status_by_task_id.get(task_id, "missing_task") for task_id in scope_task_ids)
        _expect_equal(problems, f"{stage_name}.total", stage.get("total"), len(scope_task_ids))
        _expect_equal(problems, f"{stage_name}.done", stage.get("done"), counts.get("done", 0))
        _expect_equal(problems, f"{stage_name}.todo", stage.get("todo"), counts.get("todo", 0))
        _expect_equal(problems, f"{stage_name}.in_progress", stage.get("in_progress"), counts.get("in_progress", 0))
        _expect_equal(problems, f"{stage_name}.partial", stage.get("partial", 0), counts.get("partial", 0))
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
