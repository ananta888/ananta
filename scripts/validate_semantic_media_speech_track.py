#!/usr/bin/env python3
"""Machine-readable quality gate for the semantic media/speech planning DAG."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_TRACK = ROOT / "todos/archiv/todo.ai-snake-semantic-media-speech-program.json"
SCHEMA = ROOT / "todos/todo.track.schema.json"


def _issue(pointer: str, reason_code: str, message: str) -> dict[str, str]:
    return {"json_pointer": pointer or "/", "reason_code": reason_code, "message": message}


def _escape_pointer(value: str) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _task_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(task.get("id") or "").strip(): dict(task)
        for task in list(payload.get("tasks") or [])
        if isinstance(task, dict) and str(task.get("id") or "").strip()
    }


def _split_ref(ref: str) -> tuple[str, str] | None:
    if ":" not in ref:
        return None
    file_name, task_id = (part.strip() for part in ref.split(":", 1))
    return (file_name, task_id) if file_name.endswith(".json") and task_id else None


def _cycle_nodes(edges: dict[str, set[str]]) -> list[list[str]]:
    state: dict[str, int] = {}
    stack: list[str] = []
    cycles: list[list[str]] = []

    def visit(node: str) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            start = stack.index(node)
            cycle = stack[start:] + [node]
            if cycle not in cycles:
                cycles.append(cycle)
            return
        state[node] = 1
        stack.append(node)
        for dependency in sorted(edges.get(node, set())):
            if dependency in edges:
                visit(dependency)
        stack.pop()
        state[node] = 2

    for node in sorted(edges):
        visit(node)
    return cycles


def validate_track(payload: dict[str, Any], *, track_path: Path, todos_dir: Path) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    for error in sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda item: list(item.path)):
        pointer = "/" + "/".join(_escape_pointer(part) for part in error.absolute_path)
        issues.append(_issue(pointer, "schema_validation_error", error.message))

    tasks = [dict(task) for task in list(payload.get("tasks") or []) if isinstance(task, dict)]
    task_ids = [str(task.get("id") or "").strip() for task in tasks]
    duplicates = sorted(task_id for task_id, count in Counter(task_ids).items() if task_id and count > 1)
    for task_id in duplicates:
        issues.append(_issue("/tasks", "duplicate_task_id", task_id))
    local = _task_index(payload)

    milestone_memberships: Counter[str] = Counter()
    milestone_ids: set[str] = set()
    for milestone_index, milestone in enumerate(list(payload.get("milestones") or [])):
        if not isinstance(milestone, dict):
            continue
        milestone_id = str(milestone.get("id") or "").strip()
        if milestone_id in milestone_ids:
            issues.append(_issue(f"/milestones/{milestone_index}/id", "duplicate_milestone_id", milestone_id))
        milestone_ids.add(milestone_id)
        for member_index, task_id in enumerate(list(milestone.get("task_ids") or [])):
            normalized = str(task_id).strip()
            milestone_memberships[normalized] += 1
            if normalized not in local:
                issues.append(
                    _issue(
                        f"/milestones/{milestone_index}/task_ids/{member_index}",
                        "unknown_milestone_task",
                        normalized,
                    )
                )
    for task_index, task in enumerate(tasks):
        task_id = str(task.get("id") or "").strip()
        membership_count = milestone_memberships.get(task_id, 0)
        if membership_count != 1:
            issues.append(
                _issue(
                    f"/tasks/{task_index}/milestone_id",
                    "task_milestone_membership_invalid",
                    f"{task_id} occurs in {membership_count} milestone task lists",
                )
            )
        declared = str(task.get("milestone_id") or "").strip()
        containing = [
            str(milestone.get("id") or "")
            for milestone in list(payload.get("milestones") or [])
            if isinstance(milestone, dict) and task_id in {str(item) for item in list(milestone.get("task_ids") or [])}
        ]
        if declared not in containing:
            issues.append(
                _issue(
                    f"/tasks/{task_index}/milestone_id",
                    "task_milestone_id_mismatch",
                    f"declared={declared!r} containing={containing!r}",
                )
            )

    loaded_tracks: dict[str, dict[str, dict[str, Any]]] = {track_path.name: local}

    def load_external(file_name: str) -> dict[str, dict[str, Any]] | None:
        if file_name in loaded_tracks:
            return loaded_tracks[file_name]
        candidate = todos_dir / file_name
        if not candidate.is_file():
            return None
        try:
            loaded = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        loaded_tracks[file_name] = _task_index(loaded)
        return loaded_tracks[file_name]

    edges: dict[str, set[str]] = {f"{track_path.name}:{task_id}": set() for task_id in local}
    for task_index, task in enumerate(tasks):
        source_id = str(task.get("id") or "").strip()
        source_ref = f"{track_path.name}:{source_id}"
        for dependency_index, raw_dependency in enumerate(list(task.get("depends_on") or [])):
            dependency = str(raw_dependency).strip()
            pointer = f"/tasks/{task_index}/depends_on/{dependency_index}"
            parsed = _split_ref(dependency)
            if parsed is None:
                if dependency not in local:
                    issues.append(_issue(pointer, "unknown_local_dependency", dependency))
                    continue
                edges[source_ref].add(f"{track_path.name}:{dependency}")
                continue
            target_file, target_id = parsed
            target_tasks = load_external(target_file)
            if target_tasks is None:
                issues.append(_issue(pointer, "unknown_cross_track_file", target_file))
                continue
            if target_id not in target_tasks:
                issues.append(_issue(pointer, "unknown_cross_track_task", dependency))
                continue
            edges[source_ref].add(f"{target_file}:{target_id}")

    for cycle in _cycle_nodes(edges):
        issues.append(_issue("/tasks", "dependency_cycle", " -> ".join(cycle)))

    # The existing consistency gate is intentionally reused: tasks[] remains
    # the single source of truth for all cached summaries.
    from scripts.validate_todo_consistency import validate_todo_payload

    for problem in validate_todo_payload(payload):
        field = str(problem).split(":", 1)[0].replace(".", "/")
        issues.append(_issue(f"/{field}", "derived_summary_mismatch", str(problem)))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--track", type=Path, default=DEFAULT_TRACK)
    parser.add_argument("--todos-dir", type=Path)
    args = parser.parse_args()
    track_path = args.track.resolve()
    todos_dir = (args.todos_dir or track_path.parent).resolve()
    try:
        payload = json.loads(track_path.read_text(encoding="utf-8"))
        issues = validate_track(payload, track_path=track_path, todos_dir=todos_dir)
    except (OSError, json.JSONDecodeError) as exc:
        issues = [_issue("/", "track_read_error", str(exc))]
    result = {"ok": not issues, "track": track_path.name, "issues": issues}
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if not issues else 2


if __name__ == "__main__":
    sys.exit(main())
