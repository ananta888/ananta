from __future__ import annotations

from client_surfaces.operator_tui.models import Section


SECTIONS: tuple[Section, ...] = (
    Section("dashboard", "Dashboard", True, ("health", "capabilities", "task_summary"), "degraded_panel", 2.0, 10.0),
    Section("goals", "Goals", True, ("goals", "goal_modes"), "empty_or_degraded_panel", 2.0, 15.0),
    Section("tasks", "Tasks", True, ("tasks", "timeline", "orchestration"), "empty_or_degraded_panel", 2.0, 8.0),
    Section("artifacts", "Artifacts", True, ("artifacts",), "browser_for_binary", 2.0, 20.0),
    Section("knowledge", "Knowledge", True, ("collections", "index_profiles"), "empty_or_degraded_panel", 2.5, 30.0),
    Section("config", "Config", True, ("config", "providers"), "redacted_panel", 2.0, 30.0),
    Section("templates", "Templates", True, ("blueprints", "templates"), "empty_or_degraded_panel", 2.0, 60.0),
    Section("system", "System", True, ("basic_health", "contracts", "agents"), "degraded_panel", 1.0, 5.0),
    Section("audit", "Audit", True, ("audit_logs",), "policy_degraded_panel", 2.0, 20.0),
    Section("terminal", "Terminal", True, ("targets", "sessions"), "degraded_panel", 2.0, 10.0),
    Section("help", "Help", True, ("keymap", "commands"), "local_only", 0.2, 60.0),
)


def section_ids() -> tuple[str, ...]:
    return tuple(section.id for section in SECTIONS)


def get_section(section_id: str) -> Section:
    normalized = normalize_section_id(section_id)
    for section in SECTIONS:
        if section.id == normalized:
            return section
    return SECTIONS[0]


def normalize_section_id(value: str) -> str:
    candidate = str(value or "").strip().lower()
    aliases = {
        "dash": "dashboard",
        "task": "tasks",
        "goal": "goals",
        "artifact": "artifacts",
        "tpl": "templates",
        "template": "templates",
        "blueprint": "templates",
        "blueprints": "templates",
        "sys": "system",
        "term": "terminal",
        "commands": "help",
        "?": "help",
    }
    candidate = aliases.get(candidate, candidate)
    valid = {section.id for section in SECTIONS}
    return candidate if candidate in valid else "dashboard"


def move_section(current_id: str, delta: int) -> str:
    ids = list(section_ids())
    try:
        index = ids.index(normalize_section_id(current_id))
    except ValueError:
        index = 0
    return ids[(index + delta) % len(ids)]
