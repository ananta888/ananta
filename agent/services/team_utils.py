from __future__ import annotations

def normalize_team_type_name(team_type_name: str) -> str:
    if not team_type_name:
        return ""
    normalized = team_type_name.strip()
    mapping = {
        "scrum": "Scrum",
        "kanban": "Kanban",
        "research": "Research",
        "code-repair": "Code-Repair",
        "code repair": "Code-Repair",
        "security-review": "Security-Review",
        "security review": "Security-Review",
        "release-prep": "Release-Prep",
        "release prep": "Release-Prep",
        "tdd": "TDD",
        "test-driven development": "TDD",
        "test driven development": "TDD",
        "research-evolution": "Research-Evolution",
        "research evolution": "Research-Evolution",
        "deerflow-evolver": "Research-Evolution",
        "deerflow evolver": "Research-Evolution",
    }
    return mapping.get(normalized.lower(), normalized)
