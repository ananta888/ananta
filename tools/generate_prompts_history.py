import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

# Make tools and project root importable
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir))
import sys
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.process_todos import (  # type: ignore
    ROLE_TO_CONFIG,
    render_prompt,
    load_json_file,
)

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")
TODO_FILES = [
    os.path.join(PROJECT_ROOT, "todo.json"),
    os.path.join(PROJECT_ROOT, "todo_next.json"),
]
OUTPUT_PATH = os.path.join(PROJECT_ROOT, "prompts_history.txt")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gather_todos() -> List[Tuple[str, str]]:
    items: List[Tuple[str, str]] = []
    for path in TODO_FILES:
        data = load_json_file(path) or {}
        for it in data.get("todos", []):
            role = (it.get("role") or "").strip()
            task = (it.get("task") or "").strip()
            if role and task:
                items.append((role, task))
    return items


def generate_history(verbose: bool = True) -> str:
    cfg: Dict[str, Any] = load_json_file(CONFIG_PATH) or {}
    prompt_templates: Dict[str, str] = cfg.get("prompt_templates", {})
    agents_cfg: Dict[str, Any] = cfg.get("agents", {}) if isinstance(cfg.get("agents"), dict) else {}

    lines: List[str] = []
    lines.append("prompts_history.txt – generiert aus todo.json und config.json")
    lines.append(f"Zeitpunkt (UTC): {now_iso()}")
    lines.append("")

    todos = gather_todos()
    for role_alias, task_text in todos:
        normalized = role_alias.strip().lower()
        role_key = ROLE_TO_CONFIG.get(normalized)
        template_key = role_key if role_key in prompt_templates else "default"
        template = prompt_templates.get(template_key, "{task}")

        role_purpose = None
        if role_key and isinstance(agents_cfg.get(role_key), dict):
            role_purpose = agents_cfg.get(role_key, {}).get("purpose")

        rendered = render_prompt(template, role_key or template_key, task_text, role_purpose)

        lines.append("----")
        lines.append(f"Role (todo): {role_alias} | Role (config): {role_key or template_key}")
        lines.append(f"Task: {task_text}")
        lines.append("Prompt:")
        lines.append(rendered)
        lines.append("")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    if verbose:
        print(f"Wrote {len(todos)} prompts to {OUTPUT_PATH}")
    return OUTPUT_PATH


if __name__ == "__main__":
    generate_history(verbose=True)
