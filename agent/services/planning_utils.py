from __future__ import annotations

import json
import logging
import re
from typing import Optional
from warnings import warn

from agent.services.execution_focused_planning import (
    EXECUTION_FOCUSED_GOAL_HINTS as _EXECUTION_FOCUSED_GOAL_HINTS,
)
from agent.services.execution_focused_planning import (
    build_execution_focused_goal_template as _build_execution_focused_goal_template,
)
from agent.services.execution_focused_planning import (
    match_execution_focused_goal_template,
)
from agent.services.planning_template_catalog import get_planning_template_catalog

VALID_PRIORITIES = {"high": "High", "medium": "Medium", "low": "Low"}
SUSPICIOUS_TASK_PATTERNS = [
    r"\bignore\b",
    r"\bsystem:\b",
    r"\bassistant:\b",
    r"<\|im_start\|>",
    r"<script\b",
]

def _load_goal_templates_from_catalog() -> dict[str, dict]:
    catalog = get_planning_template_catalog()
    loaded = catalog.load()
    templates: dict[str, dict] = {}
    for template in list(loaded.get("templates") or []):
        template_id = str(template.get("id") or "").strip()
        if not template_id:
            continue
        templates[template_id] = {
            "keywords": [str(item).strip() for item in list(template.get("keywords") or []) if str(item).strip()],
            "subtasks": [dict(item) for item in list(template.get("subtasks") or [])],
        }
    return templates


# Deprecated compatibility shim for legacy imports. Source of truth is the planning template catalog.
try:
    GOAL_TEMPLATES = _load_goal_templates_from_catalog()
except (OSError, ValueError):
    GOAL_TEMPLATES = {}

# Deprecated compatibility exports. Source of truth moved to execution_focused_planning.py.
EXECUTION_FOCUSED_GOAL_HINTS = _EXECUTION_FOCUSED_GOAL_HINTS
build_execution_focused_goal_template = _build_execution_focused_goal_template

PROMPT_INJECTION_PATTERNS = [
    "ignore previous",
    "ignore all",
    "disregard",
    "forget everything",
    "new instructions",
    "system:",
    "assistant:",
    "<|im_start|",
    "<|im_end|>",
    "### instruction",
    "### system",
    "act as",
    "pretend you are",
    "you are now",
    "simulate",
    "jailbreak",
    "DAN",
    "do anything now",
]


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
    depends_on = item.get("depends_on")
    if not isinstance(depends_on, list):
        depends_on = []
    normalized_depends_on = [str(dep).strip() for dep in depends_on if str(dep).strip()][:5]
    return {
        "title": title[:200],
        "description": description[:2000],
        "priority": normalize_priority(item.get("priority"), default_priority),
        "depends_on": normalized_depends_on,
    }


def extract_task_items_from_payload(payload: object) -> list[object]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    for key in ("tasks", "subtasks", "items", "steps", "children"):
        value = payload.get(key)
        if isinstance(value, list):
            return value

    nested_dependencies = payload.get("depends_on")
    if isinstance(nested_dependencies, list):
        extracted: list[object] = []
        for entry in nested_dependencies:
            if isinstance(entry, dict):
                extracted.append(
                    {
                        "title": entry.get("title") or entry.get("name") or "",
                        "description": entry.get("description") or entry.get("task") or entry.get("name") or "",
                        "priority": entry.get("priority") or payload.get("priority"),
                        "depends_on": entry.get("depends_on") if isinstance(entry.get("depends_on"), list) else [],
                    }
                )
            elif isinstance(entry, str):
                extracted.append({"description": entry, "priority": payload.get("priority")})
        if extracted:
            return extracted

    if any(str(payload.get(key) or "").strip() for key in ("title", "name", "description", "task")):
        return [payload]

    return []


def parse_subtasks_from_llm_response(response: str, default_priority: str = "Medium") -> list[dict]:
    cleaned = strip_markdown_fences(response)
    try:
        json_payload = extract_json_payload(cleaned) or cleaned
        parsed = json.loads(json_payload)
        items = extract_task_items_from_payload(parsed)
        normalized = [normalize_subtask(item, default_priority=default_priority) for item in items]
        return [item for item in normalized if item]
    except json.JSONDecodeError:
        tasks = []
        for line in cleaned.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith(("-", "*", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.")):
                desc = line.lstrip("-*1234567890. ").strip()
                normalized = normalize_subtask(
                    {"description": desc, "priority": default_priority},
                    default_priority=default_priority,
                )
                if normalized:
                    tasks.append(normalized)
        return tasks


def parse_followup_analysis(raw_response: str, default_priority: str = "Medium") -> dict:
    json_payload = extract_json_payload(raw_response)
    if not json_payload:
        return {
            "task_complete": True,
            "needs_review": False,
            "followup_tasks": [],
            "suggestions": [],
            "parse_error": True,
            "error_classification": "missing_json",
        }
    try:
        parsed = json.loads(json_payload)
    except json.JSONDecodeError:
        return {
            "task_complete": True,
            "needs_review": False,
            "followup_tasks": [],
            "suggestions": [],
            "parse_error": True,
            "error_classification": "invalid_json",
        }
    if not isinstance(parsed, dict):
        return {
            "task_complete": True,
            "needs_review": False,
            "followup_tasks": [],
            "suggestions": [],
            "parse_error": True,
            "error_classification": "wrong_shape",
        }
    followups = parsed.get("followup_tasks")
    normalized_followups = []
    if isinstance(followups, list):
        normalized_followups = [
            item
            for item in (
                normalize_subtask(entry, default_priority=default_priority) for entry in followups
            )
            if item
        ][:5]
    suggestions = parsed.get("suggestions") if isinstance(parsed.get("suggestions"), list) else []
    cleaned_suggestions = [str(item).strip()[:240] for item in suggestions if str(item).strip()][:10]
    return {
        "task_complete": bool(parsed.get("task_complete", True)),
        "needs_review": bool(parsed.get("needs_review", False)),
        "followup_tasks": normalized_followups,
        "suggestions": cleaned_suggestions,
        "parse_error": False,
    }


def match_goal_template(goal: str) -> Optional[list[dict]]:
    warn(
        (
            "planning_utils.match_goal_template is deprecated. "
            "Use PlanningTemplateCatalog/TemplatePlanningStrategy instead."
        ),
        DeprecationWarning,
        stacklevel=2,
    )
    catalog = get_planning_template_catalog()
    exact_template = catalog.get_template(str(goal or "").strip())
    if exact_template is not None:
        return list(exact_template.get("subtasks") or [])

    lower_goal = str(goal or "").lower()
    tdd_keywords = ("tdd", "test-driven", "test driven", "test-first", "red green", "red-green")
    if any(keyword in lower_goal for keyword in tdd_keywords):
        tdd_template = catalog.get_template("tdd")
        if tdd_template is not None:
            return list(tdd_template.get("subtasks") or [])

    execution_focused_subtasks = match_execution_focused_goal_template(goal)
    if execution_focused_subtasks:
        return execution_focused_subtasks

    subtasks = catalog.resolve_subtasks(goal, exact_id_first=False)
    if subtasks:
        return subtasks
    return None


def try_load_repo_context(goal: str) -> Optional[str]:
    try:
        from agent.config import settings
        from agent.hybrid_orchestrator import HybridOrchestrator

        repo_root = settings.rag_repo_root or "."
        orchestrator = HybridOrchestrator(repo_root=repo_root)
        context_result = orchestrator.get_relevant_context(goal)
        if context_result and isinstance(context_result, dict) and context_result.get("context_text"):
            return str(context_result["context_text"])[:2000]
    except Exception as exc:
        logging.debug(f"Could not load repo context: {exc}")
    return None


def build_planning_prompt(goal: str, context: Optional[str] = None, max_tasks: int = 8) -> str:
    prompt = (
        "Du bist ein Projektplanungs-Assistent. Analysiere das folgende Ziel und "
        "zerlege es in konkrete, ausfuehrbare Teilaufgaben.\n\n"
        f"ZIEL:\n{goal}\n\n"
        "ANFORDERUNGEN:\n"
        f"1. Erstelle {max_tasks} oder weniger Teilaufgaben\n"
        "2. Jede Teilaufgabe soll konkret und ausfuehrbar sein\n"
        "3. Priorisiere nach Abhaengigkeiten (was muss zuerst erledigt werden)\n"
        "4. Verwende diese Prioritaeten: High, Medium, Low\n\n"
        "AUSGABEFORMAT (nur JSON, keine Erklaerung):\n"
        "[\n"
        '  {"title": "Kurzer Titel", "description": "Detaillierte Beschreibung der Aufgabe", '
        '"priority": "High|Medium|Low", "depends_on": []},\n'
        "  ...\n"
        "]\n"
    )
    if context:
        prompt = f"{prompt}\n\nKONTEXT:\n{context}"
    return prompt


def sanitize_input(text: str, max_length: int = 4000) -> str:
    if not text:
        return ""
    sanitized = text.strip()[:max_length]
    for pattern in PROMPT_INJECTION_PATTERNS:
        if re.search(re.escape(pattern), sanitized, flags=re.IGNORECASE):
            logging.warning(f"Potential prompt injection detected: {pattern}")
            sanitized = re.sub(re.escape(pattern), "", sanitized, flags=re.IGNORECASE)
    sanitized = " ".join(sanitized.split())
    return sanitized


def validate_goal(goal: str) -> tuple[bool, str]:
    if not goal or not goal.strip():
        return False, "goal_required"
    if len(goal) > 4000:
        return False, "goal_too_long"
    lower = goal.lower()
    critical_patterns = ["ignore previous instructions", "jailbreak", "DAN mode"]
    for pattern in critical_patterns:
        if pattern.lower() in lower:
            return False, "prompt_injection_detected"
    return True, ""
