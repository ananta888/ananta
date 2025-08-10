"""
Auto-plan tasks based solely on README.md, update todo.json and todo_next.json,
append tasks_history entries, and optionally run the configured pipeline.

Usage:
  python -m tools.auto_plan_and_run            # plan + write files + run pipeline (dry-run)
  python -m tools.auto_plan_and_run --execute  # plan + write files + run pipeline (exec)
  python -m tools.auto_plan_and_run --no-run   # only plan + write files
"""
from __future__ import annotations

import argparse
import os
from typing import List, Tuple, Dict

# Ensure intra-project imports
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir))

README_PATH = os.path.join(PROJECT_ROOT, "README.md")
TODO_PATH = os.path.join(PROJECT_ROOT, "todo.json")
TODO_NEXT_PATH = os.path.join(PROJECT_ROOT, "todo_next.json")

# Reuse helpers
from tools.process_todos import (
    ROLE_TO_CONFIG,
    FOLLOW_UP_SUGGESTIONS,
    append_history,
    save_json_file,
)
from tools.run_pipeline import run_pipeline


def _read_readme_text() -> str:
    try:
        with open(README_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def derive_tasks_from_readme(txt: str) -> List[Tuple[str, str]]:
    """
    Return a list of (role_alias, task) derived ONLY from README.md content.
    Heuristics are simple keyword/section matches tailored to this repo's README.
    """
    tasks: List[Tuple[str, str]] = []

    # Normalize for keyword search
    low = txt.lower()

    # High-Level Objectives
    if "high-level objectives" in low:
        # Architect
        tasks.append(
            (
                "architect",
                "Create UML diagrams and add them under architektur/uml/ with references in architektur/README.md",
            )
        )
        # Backend
        tasks.append(
            (
                "back-end developer",
                "Add backend overview by creating src/README.md and updating root README link",
            )
        )
        # Frontend
        tasks.append(
            (
                "front-end developer",
                "Extend dashboard documentation with environment setup details and API examples",
            )
        )
        # DevOps
        tasks.append(
            (
                "devop",
                "Document test environment and Docker instructions for Playwright tests including RUN_TESTS variable",
            )
        )
        # QA
        tasks.append(
            (
                "qa/test engineer",
                "Add unit tests for common/http_client.py covering success, JSON/non-JSON responses, and timeout/retry behavior; document pytest execution in README-TESTS.md",
            )
        )
        # Product Owner
        tasks.append(
            (
                "product owner",
                "Draft a product roadmap and include high-level objectives in root documentation",
            )
        )
        # Fullstack reviewer
        tasks.append(
            (
                "fullstack reviewer",
                "Review and standardize documentation; remove duplicate sections in root README",
            )
        )

    # Security headers section
    if "security headers" in low:
        tasks.append(
            (
                "fullstack reviewer",
                "Audit security headers in controller responses and document required headers (CSP, HSTS, X-Frame-Options, Referrer-Policy)",
            )
        )

    # De-duplicate while preserving order
    seen = set()
    deduped: List[Tuple[str, str]] = []
    for role, task in tasks:
        key = (role.strip().lower(), task.strip())
        if key in seen:
            continue
        seen.add(key)
        deduped.append((role, task))
    return deduped


def _invert_role_mapping() -> Dict[str, str]:
    """Map config role -> a preferred alias used in our todos."""
    # Choose the first alias pointing to that config role, with a preference for existing aliases in repo
    preferred: Dict[str, str] = {}
    priority = [
        "architect",
        "back-end developer",
        "frontend developer",
        "front-end developer",
        "fullstack reviewer",
        "devop",
        "devops",
        "qa/test engineer",
        "product owner",
        "scrum master",
    ]
    # Build reverse bucket
    buckets: Dict[str, List[str]] = {}
    for alias, cfg in ROLE_TO_CONFIG.items():
        buckets.setdefault(cfg, []).append(alias)
    for cfg, aliases in buckets.items():
        # pick best alias by our priority list
        chosen = None
        for p in priority:
            if p in aliases:
                chosen = p
                break
        if not chosen:
            chosen = aliases[0]
        preferred[cfg] = chosen
    return preferred


def write_todo_files(tasks: List[Tuple[str, str]]) -> None:
    # Write todo.json
    todos_list = [{"task": t, "role": r} for (r, t) in tasks]
    save_json_file(TODO_PATH, {"todos": todos_list})

    # Prepare todo_next.json from FOLLOW_UP_SUGGESTIONS
    cfg_to_alias = _invert_role_mapping()
    next_items: List[Dict[str, str]] = []
    for cfg_role, suggs in FOLLOW_UP_SUGGESTIONS.items():
        alias = cfg_to_alias.get(cfg_role)
        if not alias:
            continue
        for s in suggs:
            s = s.strip()
            if s:
                next_items.append({"role": alias, "task": s})

    # Deduplicate
    seen = set()
    deduped_items: List[Dict[str, str]] = []
    for item in next_items:
        key = (item["role"].lower(), item["task"])  # case-insensitive role
        if key in seen:
            continue
        seen.add(key)
        deduped_items.append(item)

    save_json_file(TODO_NEXT_PATH, {"todos": deduped_items})


def append_history_for_tasks(tasks: List[Tuple[str, str]]) -> None:
    # Group by alias for append_history
    grouped: Dict[str, List[str]] = {}
    for alias, task in tasks:
        grouped.setdefault(alias.strip().lower(), []).append(task)
    for alias, items in grouped.items():
        try:
            append_history(alias, items)
        except Exception:
            # history append should never block the flow
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan tasks from README and run pipeline")
    parser.add_argument("--execute", action="store_true", help="Call API endpoints instead of dry-run")
    parser.add_argument("--no-run", action="store_true", help="Only write todos and history; do not run pipeline")
    args = parser.parse_args()

    readme = _read_readme_text()
    tasks = derive_tasks_from_readme(readme)

    write_todo_files(tasks)
    append_history_for_tasks(tasks)

    if not args.no_run:
        # Run the pipeline to generate prompts and artifacts
        run_pipeline(dry_run=(not args.execute), verbose=True)


if __name__ == "__main__":
    main()
