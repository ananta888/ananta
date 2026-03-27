from __future__ import annotations

import json
import logging
import re
from typing import Optional

VALID_PRIORITIES = {"high": "High", "medium": "Medium", "low": "Low"}
SUSPICIOUS_TASK_PATTERNS = [
    r"\bignore\b",
    r"\bsystem:\b",
    r"\bassistant:\b",
    r"<\|im_start\|>",
    r"<script\b",
]

GOAL_TEMPLATES = {
    "bug_fix": {
        "keywords": ["bug", "fix", "fehler", "error", "crash", "broken", "kaputt"],
        "subtasks": [
            {
                "title": "Bug reproduzieren",
                "description": "Schritte zum Reproduzieren dokumentieren und verifizieren",
                "priority": "High",
            },
            {"title": "Root Cause Analyse", "description": "Ursache des Fehlers identifizieren", "priority": "High"},
            {"title": "Fix implementieren", "description": "Korrektur implementieren", "priority": "High"},
            {
                "title": "Test schreiben",
                "description": "Unit/Integration Test für den Bug-Fix erstellen",
                "priority": "Medium",
            },
            {"title": "Code Review", "description": "Fix zur Überprüfung einreichen", "priority": "Medium"},
        ],
    },
    "feature": {
        "keywords": ["feature", "implement", "add", "neu", "new", "create", "erstellen"],
        "subtasks": [
            {
                "title": "Anforderungen definieren",
                "description": "Funktionale und nicht-funktionale Anforderungen dokumentieren",
                "priority": "High",
            },
            {"title": "Design/Architektur", "description": "Technisches Design erstellen", "priority": "High"},
            {"title": "Implementierung", "description": "Feature implementieren", "priority": "High"},
            {"title": "Tests schreiben", "description": "Unit und Integration Tests erstellen", "priority": "Medium"},
            {"title": "Dokumentation", "description": "Feature dokumentieren", "priority": "Low"},
        ],
    },
    "refactor": {
        "keywords": ["refactor", "cleanup", "improve", "optimieren", "verbessern", "clean"],
        "subtasks": [
            {
                "title": "Code-Analyse",
                "description": "Aktuellen Stand analysieren und Verbesserungspotenzial identifizieren",
                "priority": "Medium",
            },
            {"title": "Refactoring-Plan", "description": "Schritte für das Refactoring planen", "priority": "Medium"},
            {"title": "Refactoring durchführen", "description": "Code umstrukturieren", "priority": "Medium"},
            {
                "title": "Tests verifizieren",
                "description": "Sicherstellen dass alle Tests noch durchlaufen",
                "priority": "High",
            },
        ],
    },
    "test": {
        "keywords": ["test", "testing", "coverage", "unit test", "integration test"],
        "subtasks": [
            {"title": "Test-Strategie", "description": "Test-Strategie und Abdeckung definieren", "priority": "High"},
            {"title": "Unit Tests", "description": "Unit Tests schreiben", "priority": "High"},
            {"title": "Integration Tests", "description": "Integration Tests implementieren", "priority": "Medium"},
            {
                "title": "Coverage-Report",
                "description": "Test-Abdeckung analysieren und dokumentieren",
                "priority": "Low",
            },
        ],
    },
}

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


def parse_subtasks_from_llm_response(response: str, default_priority: str = "Medium") -> list[dict]:
    cleaned = strip_markdown_fences(response)
    try:
        json_payload = extract_json_payload(cleaned) or cleaned
        parsed = json.loads(json_payload)
        if isinstance(parsed, dict):
            parsed = parsed.get("tasks") or parsed.get("subtasks") or parsed.get("items") or []
        if not isinstance(parsed, list):
            return []
        normalized = [normalize_subtask(item, default_priority=default_priority) for item in parsed]
        return [item for item in normalized if item]
    except json.JSONDecodeError:
        tasks = []
        for line in cleaned.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith(("-", "*", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.")):
                desc = line.lstrip("-*1234567890. ").strip()
                normalized = normalize_subtask({"description": desc, "priority": default_priority}, default_priority=default_priority)
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
        normalized_followups = [item for item in (normalize_subtask(entry, default_priority=default_priority) for entry in followups) if item][:5]
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
    lower_goal = goal.lower()
    for template in GOAL_TEMPLATES.values():
        for keyword in template["keywords"]:
            if keyword.lower() in lower_goal:
                return template["subtasks"]
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
