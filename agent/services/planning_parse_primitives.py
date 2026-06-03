from __future__ import annotations

import re
from typing import Any

VALID_PRIORITIES = {"high": "High", "medium": "Medium", "low": "Low"}
SUSPICIOUS_TASK_PATTERNS = [
    r"\bignore\b",
    r"\bsystem:\b",
    r"\bassistant:\b",
    r"<\|im_start\|>",
    r"<script\b",
]
_ACTIONABLE_VERBS = (
    "implement",
    "create",
    "write",
    "run",
    "test",
    "verify",
    "configure",
    "update",
    "add",
    "build",
)


def strip_markdown_fences(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines[-1].startswith("```"):
            cleaned = "\n".join(lines[1:-1])
        else:
            cleaned = "\n".join(lines[1:])
    return cleaned.strip()


def extract_json_payload(text: str) -> str | None:
    cleaned = strip_markdown_fences(text)
    if not cleaned:
        return None
    first_brace = cleaned.find("{")
    first_bracket = cleaned.find("[")
    if first_brace == -1 and first_bracket == -1:
        return None
    if first_brace == -1:
        start = first_bracket
        end = cleaned.rfind("]")
    elif first_bracket == -1:
        start = first_brace
        end = cleaned.rfind("}")
    else:
        start = min(first_brace, first_bracket)
        end = cleaned.rfind("}" if start == first_brace else "]")
    if start < 0 or end < start:
        return None
    return cleaned[start : end + 1].strip()


def contains_suspicious_text(value: str) -> bool:
    lower = str(value or "").strip().lower()
    if not lower:
        return False
    return any(re.search(pattern, lower) for pattern in SUSPICIOUS_TASK_PATTERNS)


def normalize_priority(value: str | None, default_priority: str = "Medium") -> str:
    raw = str(value or "").strip().lower()
    if raw in VALID_PRIORITIES:
        return VALID_PRIORITIES[raw]
    return VALID_PRIORITIES.get(str(default_priority or "").strip().lower(), "Medium")


def normalize_subtask(item: dict, default_priority: str = "Medium") -> dict | None:
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or item.get("name") or "").strip()
    description = str(item.get("description") or item.get("task") or title).strip()
    if not title:
        title = description[:80].strip()
    if not title or not description:
        return None
    if contains_suspicious_text(title) or contains_suspicious_text(description):
        return None
    desc_l = description.lower()
    if len(desc_l) < 16:
        return None
    if not any(v in desc_l for v in _ACTIONABLE_VERBS):
        return None
    depends_on = item.get("depends_on")
    if not isinstance(depends_on, list):
        depends_on = []
    normalized_depends_on = [str(dep).strip() for dep in depends_on if str(dep).strip()][:5]
    dependency_mode = str(item.get("dependency_mode") or "").strip().lower()
    if dependency_mode not in {"parallel", "explicit", "sequential"}:
        dependency_mode = "explicit" if normalized_depends_on else "sequential"
    if "__parallel__" in normalized_depends_on:
        dependency_mode = "parallel"
        normalized_depends_on = []
    return {
        "title": title[:200],
        "description": description[:2000],
        "priority": normalize_priority(item.get("priority"), default_priority),
        "depends_on": normalized_depends_on,
        "dependency_mode": dependency_mode,
    }


def extract_task_items_from_payload(payload: object) -> list[object]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    for key in ("tasks", "subtasks", "sub_tasks", "items", "steps", "children"):
        value = payload.get(key)
        if isinstance(value, list):
            return value

    actionable_steps = payload.get("actionable_steps")
    if isinstance(actionable_steps, list):
        extracted_steps: list[object] = []
        for step in actionable_steps:
            if isinstance(step, dict):
                extracted_steps.append(
                    {
                        "title": step.get("title") or step.get("name") or step.get("step") or "",
                        "description": step.get("detail") or step.get("description") or step.get("title") or "",
                        "priority": step.get("priority") or payload.get("priority"),
                        "depends_on": step.get("depends_on") or [],
                    }
                )
            elif isinstance(step, str):
                extracted_steps.append(
                    {
                        "title": step[:80],
                        "description": step,
                        "priority": payload.get("priority"),
                    }
                )
        if extracted_steps:
            return extracted_steps

    return []
