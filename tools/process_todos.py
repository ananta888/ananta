import json
import os
import re
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, Optional

# from src.config import ConfigManager  # removed to avoid import issues

ROOT = os.path.dirname(os.path.abspath(__file__))
# tools directory -> project root
PROJECT_ROOT = os.path.abspath(os.path.join(ROOT, os.pardir))
TASKS_HISTORY_DIR = os.path.join(PROJECT_ROOT, "tasks_history")
TODO_FILES = [
    os.path.join(PROJECT_ROOT, "todo.json"),
    os.path.join(PROJECT_ROOT, "todo_next.json"),
]
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")
PENDING_DIR = os.path.join(TASKS_HISTORY_DIR, "pending")

# Maps lower-case role aliases from todo.json to config.json agent keys
ROLE_TO_CONFIG: Dict[str, str] = {
    "architect": "Architect",
    "back-end developer": "Backend Developer",
    "backend developer": "Backend Developer",
    "front-end developer": "Frontend Developer",
    "frontend developer": "Frontend Developer",
    "fullstack reviewer": "Fullstack Reviewer",
    "devop": "DevOps Engineer",
    "devops": "DevOps Engineer",
    "product owner": "Scrum Master / Product Owner",
    "scrum master": "Scrum Master / Product Owner",
    "qa/test engineer": "QA/Test Engineer",
    "qa engineer": "QA/Test Engineer",
}

# Simple follow-up suggestions per config role
FOLLOW_UP_SUGGESTIONS: Dict[str, List[str]] = {
    "Architect": [
        "Create class diagram for core backend entities",
        "Add references from architektur/README.md to all UML files",
    ],
    "Backend Developer": [
        "Extend src/README.md with example authentication middleware and usage",
        "Add database migration instructions and sample commands",
    ],
    "Frontend Developer": [
        "Include accessibility audit steps (e.g., Lighthouse) in frontend docs",
        "Add state management examples and component screenshots",
    ],
    "Fullstack Reviewer": [
        "Standardize headings and terminology across docs",
        "Introduce automated linting and security header checks",
    ],
    "DevOps Engineer": [
        "Integrate Playwright tests into CI with caching",
        "Configure Docker image layer caching in CI",
    ],
    "QA/Test Engineer": [
        "Create E2E tests for authentication flow",
        "Add cross-browser tests and stress scenarios",
    ],
    "Scrum Master / Product Owner": [
        "Break down roadmap objectives into prioritized user stories",
        "Define acceptance criteria and Sprint plan (2 weeks)",
    ],
}


def role_to_history_filename(role_alias: str) -> str:
    """Convert a role alias from todo.json to tasks_history filename."""
    base = role_alias.strip().lower()
    # spaces and slashes become underscores; keep hyphens
    base = base.replace("/", "_").replace(" ", "_")
    # remove characters that are risky for filenames
    base = re.sub(r"[^a-z0-9_\-]", "", base)
    return f"{base}.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json_file(path: str) -> Any:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json_file(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


class SafeDict(dict):
    def __missing__(self, key):
        # Preserve placeholder if missing
        return "{" + key + "}"


def render_prompt(template: str, role_key: str, task_text: str, role_purpose: Optional[str] = None) -> str:
    """Render config template by converting {{var}} to {var},
    injecting role description if available, and filling sensible defaults.
    """
    # Convert double-curly to single for Python format
    tmpl = template.replace("{{", "{").replace("}}", "}")
    # Default values per role
    defaults: Dict[str, str] = {
        "anforderungen": task_text,
        "endpoint_name": "N/A",
        "beschreibung": task_text,
        "sprache": "Python",
        "funktion": task_text,
        "api_details": "Siehe API-Spezifikation im Projekt.",
        "feature_name": task_text,
        "purpose": role_purpose or "",
        "rollenbeschreibung": role_purpose or "",
    }
    # Heuristics for endpoint name
    if role_key == "Backend Developer":
        # Try to infer an endpoint name from the task text.
        # 1) Pattern: "endpoint <name>"
        m = re.search(r"endpoint\s+([A-Za-z0-9_\-/<>]+)", task_text, re.IGNORECASE)
        if m:
            defaults["endpoint_name"] = m.group(1)
        else:
            # 2) First explicit path like /issues or /agent/<name>/log
            m2 = re.search(r"(/[^\s,;\)]+)", task_text)
            if m2:
                defaults["endpoint_name"] = m2.group(1)
            else:
                defaults["endpoint_name"] = "example"
    rendered_core = tmpl.format_map(SafeDict(defaults))
    # Prepend human-readable role description if provided
    if role_purpose:
        prefix = f"Rollenbeschreibung ({role_key}): {role_purpose}\n\n"
        return prefix + rendered_core
    return rendered_core


def append_history(role_alias: str, entries: List[str]) -> None:
    """Append tasks to tasks_history/<role_alias>.json with timestamp, skipping duplicates."""
    filename = role_to_history_filename(role_alias)
    path = os.path.join(TASKS_HISTORY_DIR, filename)
    existing: List[Dict[str, str]] = []
    if os.path.exists(path):
        try:
            existing = load_json_file(path) or []
        except Exception:
            existing = []
    existing_tasks = {e.get("task") for e in existing if isinstance(e, dict)}

    for t in entries:
        if t in existing_tasks:
            continue
        existing.append({"task": t, "date": now_iso()})
    save_json_file(path, existing)


def create_pending_file(task_text: str, role_alias: str, reason: str, suggestions: List[Tuple[str, str]]) -> str:
    """Create a pending task file with description in filename and per-agent subtasks.

    suggestions: list of (agent_alias, subtask_text)
    Returns the path to the created file.
    """
    os.makedirs(PENDING_DIR, exist_ok=True)
    safe_name = re.sub(r"[^a-z0-9_\-]", "_", task_text.lower())
    filename = f"pending_{safe_name[:60]}.json"
    path = os.path.join(PENDING_DIR, filename)
    data = {
        "original_task": task_text,
        "role": role_alias,
        "reason": reason,
        "created": now_iso(),
        "assigned_subtasks": [
            {"role": alias, "task": subtask} for alias, subtask in suggestions
        ],
    }
    save_json_file(path, data)
    return path


def process_todos(verbose: bool = True) -> None:
    cfg = load_json_file(CONFIG_PATH) or {}
    prompt_templates: Dict[str, str] = cfg.get("prompt_templates", {})

    # Collect all tasks from available todo files
    all_todos: List[Tuple[str, str]] = []  # (role_alias, task)
    for todo_path in TODO_FILES:
        data = load_json_file(todo_path)
        if not data:
            continue
        for item in data.get("todos", []):
            task = item.get("task")
            role_alias = (item.get("role") or "").strip()
            if task and role_alias:
                all_todos.append((role_alias, task))

    if verbose:
        print(f"Found {len(all_todos)} todo items from {len(TODO_FILES)} files.")

    for role_alias, task_text in all_todos:
        normalized = role_alias.strip().lower()
        role_key = ROLE_TO_CONFIG.get(normalized)
        template_key = role_key if role_key in prompt_templates else "default"
        template = prompt_templates.get(template_key, "{task}")

        # Render the prompt (for traceability we print in verbose mode)
        agents_cfg: Dict[str, Any] = cfg.get("agents", {}) if isinstance(cfg.get("agents"), dict) else {}
        role_purpose = None
        if role_key and isinstance(agents_cfg.get(role_key), dict):
            role_purpose = agents_cfg.get(role_key, {}).get("purpose")
        rendered = render_prompt(template, role_key or template_key, task_text, role_purpose)
        if verbose:
            print("\n---\nRole:", role_alias,
                  "\nConfig Role:", role_key or template_key,
                  "\nTask:", task_text,
                  "\nApplied Prompt:\n", rendered)

        # Decide if task is completable: if we have at least a template, consider it processed
        if not template:
            # Create pending file with basic suggestions
            suggestions = []
            for cfg_role, followups in FOLLOW_UP_SUGGESTIONS.items():
                # map config role back to alias style for consistency
                alias_guess = cfg_role.lower().replace("/", "_")
                if followups:
                    suggestions.append((alias_guess, followups[0]))
            pending_path = create_pending_file(task_text, normalized, "No template available to process task.", suggestions)
            if verbose:
                print(f"Created pending file: {pending_path}")
            continue

        # Append the original task to history for the role
        append_history(normalized, [task_text])

        # Generate follow-ups and append them
        followups = FOLLOW_UP_SUGGESTIONS.get(role_key or template_key, [])
        if followups:
            append_history(normalized, followups)


if __name__ == "__main__":
    process_todos(verbose=True)
