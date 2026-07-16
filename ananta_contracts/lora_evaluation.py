"""Deterministic, dependency-free scoring shared by Hub and LoRA workers."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

TODO_SCORERS = frozenset({"todo_json", "ananta_todo_json"})
SUPPORTED_SCORERS = frozenset({"generic", *TODO_SCORERS})


def score_evaluation_output(
    scorer_name: str,
    output_text: str,
    *,
    expected_output: str = "",
) -> dict[str, Any]:
    """Score bounded generated text without executing or importing it."""

    normalized = str(scorer_name or "generic").strip().lower()
    if normalized in TODO_SCORERS:
        return score_todo_track_output(output_text)
    if normalized != "generic":
        raise ValueError(f"unsupported evaluation scorer: {normalized}")
    text = str(output_text or "").strip()
    expected = str(expected_output or "").strip()
    exact_match = bool(expected) and text.casefold() == expected.casefold()
    total = 1.0 if exact_match else (0.25 if text and not expected else 0.0)
    return {
        "non_empty": bool(text),
        "expected_present": bool(expected),
        "exact_match": exact_match,
        "total": total,
    }


def score_todo_track_output(output_text: str) -> dict[str, Any]:
    """Score the current Ananta Todo-track shape, including cross references."""

    parsed = _parse_json_object(str(output_text or ""))
    if parsed is None:
        return {
            "json_valid": False,
            "has_track": False,
            "has_owner": False,
            "has_status_scale": False,
            "has_priority_scale": False,
            "has_risk_scale": False,
            "has_milestones": False,
            "has_tasks": False,
            "milestone_quality_ratio": 0.0,
            "task_quality_ratio": 0.0,
            "cross_references_valid": False,
            "total": 0.0,
        }

    milestones = parsed.get("milestones") if isinstance(parsed.get("milestones"), list) else []
    tasks = parsed.get("tasks") if isinstance(parsed.get("tasks"), list) else []
    milestone_quality = [_milestone_valid(item) for item in milestones]
    task_quality = [_task_valid(item) for item in tasks]
    scores: dict[str, Any] = {
        "json_valid": True,
        "has_track": bool(str(parsed.get("track") or "").strip()),
        "has_owner": bool(str(parsed.get("owner") or "").strip()),
        "has_status_scale": _non_empty_scale(parsed.get("status_scale")),
        "has_priority_scale": _non_empty_scale(parsed.get("priority_scale")),
        "has_risk_scale": _non_empty_scale(parsed.get("risk_scale")),
        "has_milestones": bool(milestones),
        "has_tasks": bool(tasks),
        "milestone_quality_ratio": (
            sum(milestone_quality) / len(milestone_quality) if milestone_quality else 0.0
        ),
        "task_quality_ratio": sum(task_quality) / len(task_quality) if task_quality else 0.0,
        "cross_references_valid": _cross_references_valid(milestones, tasks),
    }
    total = (
        0.20
        + 0.05 * int(scores["has_track"])
        + 0.05 * int(scores["has_owner"])
        + 0.05 * int(scores["has_status_scale"])
        + 0.05 * int(scores["has_priority_scale"])
        + 0.05 * int(scores["has_risk_scale"])
        + 0.05 * int(scores["has_milestones"])
        + 0.05 * int(scores["has_tasks"])
        + 0.10 * float(scores["milestone_quality_ratio"])
        + 0.25 * float(scores["task_quality_ratio"])
        + 0.10 * int(scores["cross_references_valid"])
    )
    return {**scores, "total": round(min(total, 1.0), 4)}


def _parse_json_object(text: str) -> dict[str, Any] | None:
    candidates = [text.strip()]
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match and match.group() not in candidates:
        candidates.append(match.group())
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _non_empty_scale(value: Any) -> bool:
    return isinstance(value, (list, Mapping)) and bool(value)


def _milestone_valid(value: Any) -> bool:
    return bool(
        isinstance(value, Mapping)
        and all(str(value.get(key) or "").strip() for key in ("id", "title", "status"))
        and isinstance(value.get("task_ids"), list)
    )


def _task_valid(value: Any) -> bool:
    return bool(
        isinstance(value, Mapping)
        and all(
            str(value.get(key) or "").strip()
            for key in ("id", "title", "status", "priority", "risk")
        )
        and isinstance(value.get("acceptance_criteria"), list)
        and bool(value.get("acceptance_criteria"))
        and isinstance(value.get("test_expectations"), list)
        and bool(value.get("test_expectations"))
    )


def _cross_references_valid(milestones: list[Any], tasks: list[Any]) -> bool:
    if not milestones or not tasks:
        return False
    milestone_ids = [str(item.get("id") or "") for item in milestones if isinstance(item, Mapping)]
    task_ids = [str(item.get("id") or "") for item in tasks if isinstance(item, Mapping)]
    if len(milestone_ids) != len(set(milestone_ids)) or len(task_ids) != len(set(task_ids)):
        return False
    known_tasks = set(task_ids)
    referenced: set[str] = set()
    for milestone in milestones:
        if not isinstance(milestone, Mapping) or not isinstance(milestone.get("task_ids"), list):
            return False
        children = [str(item or "") for item in milestone["task_ids"]]
        if any(child not in known_tasks for child in children):
            return False
        referenced.update(children)
    task_milestones = {
        str(item.get("milestone_id") or "")
        for item in tasks
        if isinstance(item, Mapping) and item.get("milestone_id") is not None
    }
    return referenced == known_tasks and (not task_milestones or task_milestones.issubset(set(milestone_ids)))


__all__ = [
    "SUPPORTED_SCORERS",
    "TODO_SCORERS",
    "score_evaluation_output",
    "score_todo_track_output",
]
