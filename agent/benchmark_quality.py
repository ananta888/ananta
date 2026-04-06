from __future__ import annotations

import re
from typing import Any

_TASK_KEYWORDS = {
    "planning": {"plan", "milestone", "sprint", "timeline", "phase", "dependency", "roadmap"},
    "research": {"pros", "cons", "trade-off", "comparison", "recommendation", "risk", "evidence"},
    "coding": {"def ", "class ", "function", "return", "import ", "const ", "let ", "```"},
    "review": {"issue", "risk", "improve", "recommend", "security", "performance", "bug"},
    "testing": {"test", "assert", "case", "coverage", "fixture", "edge", "integration"},
    "ops": {"docker", "ci", "cd", "kubernetes", "deploy", "container", "pipeline"},
    "analysis": {"architecture", "constraint", "trade-off", "option", "component", "decision"},
    "doc": {"summary", "section", "usage", "example", "document", "guide"},
}

_ROLE_KEYWORDS = {
    "planner": {"sprint", "milestone", "backlog"},
    "researcher": {"compare", "evidence", "recommendation"},
    "coder": {"function", "implementation", "code"},
    "reviewer": {"issue", "bug", "improve"},
    "tester": {"assert", "test", "coverage"},
    "devops": {"docker", "deployment", "pipeline"},
    "architect": {"architecture", "component", "boundary"},
    "scrum_master": {"retrospective", "team", "blocker"},
}

_MIN_WORDS = {
    "planning": 18,
    "research": 18,
    "coding": 10,
    "review": 14,
    "testing": 14,
    "ops": 14,
    "analysis": 16,
    "doc": 12,
}


def evaluate_benchmark_response_quality(
    text: str,
    *,
    task_kind: str | None = None,
    role_name: str | None = None,
) -> dict[str, Any]:
    normalized_task = str(task_kind or "analysis").strip().lower() or "analysis"
    normalized_role = str(role_name or "").strip().lower()
    normalized_text = str(text or "").strip()
    lowered = normalized_text.lower()
    words = len(re.findall(r"\b\w+\b", normalized_text))

    if not normalized_text:
        return {
            "passed": False,
            "score": 0.0,
            "reason": "empty_response",
            "details": {"word_count": 0, "structure_hits": 0, "task_keyword_hits": 0, "role_keyword_hits": 0},
        }

    lines = [line.strip() for line in normalized_text.splitlines() if line.strip()]
    structure_hits = 0
    if len(lines) >= 3:
        structure_hits += 1
    if any(line.startswith(("-", "*", "1.", "2.", "3.")) for line in lines):
        structure_hits += 1
    if "```" in normalized_text:
        structure_hits += 1
    if "\n\n" in normalized_text:
        structure_hits += 1

    task_keywords = _TASK_KEYWORDS.get(normalized_task, _TASK_KEYWORDS["analysis"])
    role_keywords = _ROLE_KEYWORDS.get(normalized_role, set())
    task_keyword_hits = sum(1 for keyword in task_keywords if keyword in lowered)
    role_keyword_hits = sum(1 for keyword in role_keywords if keyword in lowered)
    min_words = _MIN_WORDS.get(normalized_task, 12)

    word_component = min(1.0, words / max(1, min_words))
    structure_component = min(1.0, structure_hits / 2.0)
    task_component = min(1.0, task_keyword_hits / 2.0)
    role_component = 1.0 if not role_keywords else min(1.0, role_keyword_hits / 1.0)

    score = round(
        (0.4 * word_component + 0.25 * structure_component + 0.25 * task_component + 0.10 * role_component) * 100.0,
        2,
    )
    passed = score >= 55.0 and words >= max(6, int(min_words * 0.7)) and (task_keyword_hits > 0 or structure_hits > 0)
    return {
        "passed": passed,
        "score": score,
        "reason": "passed" if passed else "insufficient_quality_evidence",
        "details": {
            "word_count": words,
            "structure_hits": structure_hits,
            "task_keyword_hits": task_keyword_hits,
            "role_keyword_hits": role_keyword_hits,
            "task_kind": normalized_task,
            "role_name": normalized_role or None,
        },
    }
