from __future__ import annotations

from typing import Any

EXECUTION_FOCUSED_GOAL_HINTS = (
    "python",
    "javascript",
    "typescript",
    "angular",
    "react",
    "flask",
    "fastapi",
    "django",
    "helper",
    "function",
    "module",
    "class",
    "api",
    "endpoint",
    "pytest",
    "unit test",
    "integration test",
    "changed files",
    "validation",
    "summary",
    "codebase",
    "repo",
    "repository",
)


def build_execution_focused_goal_template(goal: str) -> list[dict[str, Any]]:
    lower_goal = str(goal or "").lower()
    subject = "die angeforderte Aenderung"
    if "fibonacci" in lower_goal:
        subject = "den Python-Fibonacci-Helper"
    elif "python" in lower_goal:
        subject = "die Python-Implementierung"
    return [
        {
            "title": f"{subject} implementieren",
            "description": (
                f"Implementiere {subject} mit klarer Schnittstelle, sinnvoller Fehlerbehandlung "
                "und produktionsnaher Struktur."
            ),
            "priority": "High",
        },
        {
            "title": "Automatisierte Tests ergaenzen",
            "description": "Erstelle Unit Tests mit pytest fuer Basisfaelle, typische Eingaben und relevante Randfaelle.",
            "priority": "High",
            "depends_on": ["1"],
        },
        {
            "title": "Tests ausfuehren und validieren",
            "description": "Fuehre die relevanten Tests aus, pruefe das Ergebnis und halte die Validierung knapp fest.",
            "priority": "Medium",
            "depends_on": ["2"],
        },
        {
            "title": "Geaenderte Dateien zusammenfassen",
            "description": "Erstelle eine kurze Zusammenfassung der geaenderten Dateien und der wichtigsten Umsetzungsergebnisse.",
            "priority": "Low",
            "depends_on": ["3"],
        },
    ]


def match_execution_focused_goal_template(goal: str) -> list[dict[str, Any]] | None:
    lower_goal = str(goal or "").lower()
    if not lower_goal:
        return None
    if any(hint in lower_goal for hint in EXECUTION_FOCUSED_GOAL_HINTS):
        return build_execution_focused_goal_template(goal)
    return None
