"""ananta task — task inspection and management."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from typing import Any

SUBCOMMANDS = ["inspect", "list", "create", "run", "cancel"]

_DEFAULT_BASE_URL = "http://localhost:5000"
_DEFAULT_USER = "admin"
_DEFAULT_PASSWORD = "test123"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ananta task",
        description="Inspect and manage Ananta tasks.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ananta task inspect --task-id <id>\n"
            "  ananta task list --goal-id <goal-id>\n"
            "  ananta task inspect --task-id <id> --json\n"
        ),
    )
    _configure_subparsers(p)
    return p


def _configure_subparsers(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="task_cmd", metavar="<action>")

    ins_p = sub.add_parser("inspect", help="Inspect a task's prompt traces and latest response.")
    ins_p.add_argument("task_id", nargs="?", default="",
                       help="Task ID or prefix. Omit for interactive selection.")
    ins_p.add_argument("--task-id", dest="task_id_opt", default="",
                       help="Task ID or prefix (alias for positional task_id).")
    ins_p.add_argument("--goal-id", dest="goal_id", default="",
                       help="Filter interactive list by goal ID.")
    ins_p.add_argument("--json", action="store_true")
    ins_p.add_argument("--base-url", default=None)
    ins_p.add_argument("--user", default=_DEFAULT_USER)
    ins_p.add_argument("--password", default=_DEFAULT_PASSWORD)

    li_p = sub.add_parser("list", help="List tasks, optionally filtered by goal.")
    li_p.add_argument("--goal-id", dest="goal_id", default="", help="Filter by goal ID.")
    li_p.add_argument("--limit", type=int, default=20)
    li_p.add_argument("--json", action="store_true")
    li_p.add_argument("--base-url", default=None)
    li_p.add_argument("--user", default=_DEFAULT_USER)
    li_p.add_argument("--password", default=_DEFAULT_PASSWORD)

    cr_p = sub.add_parser("create", help="[MUTATING] Create a task (not yet implemented).")
    cr_p.add_argument("--goal-id", dest="goal_id", required=True)
    cr_p.add_argument("--title", required=True)

    run_p = sub.add_parser("run", help="[MUTATING] Trigger execution of a task (not yet implemented).")
    run_p.add_argument("--task-id", dest="task_id", required=True)

    cancel_p = sub.add_parser("cancel", help="[MUTATING] Cancel a running task (not yet implemented).")
    cancel_p.add_argument("--task-id", dest="task_id", required=True)


def dispatch(argv: Sequence[str]) -> int:
    parser = _build_parser()
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    try:
        parsed = parser.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    cmd = parsed.task_cmd
    if cmd == "inspect":
        return _cmd_inspect(parsed)
    if cmd == "list":
        return _cmd_list(parsed)
    if cmd in ("create", "run", "cancel"):
        return _not_implemented(f"task {cmd}")
    parser.print_help()
    return 0


def register(subparsers: Any) -> None:
    p = subparsers.add_parser("task", help="Inspect and manage Ananta tasks.")
    _configure_subparsers(p)
    p.set_defaults(_dispatch=dispatch)


# ── Implementations ────────────────────────────────────────────────────────────

def _not_implemented(name: str) -> int:
    print(f"Error: 'ananta {name}' is not yet implemented.", file=sys.stderr)
    return 1


def _base_url(parsed) -> str:
    return (
        getattr(parsed, "base_url", None)
        or os.environ.get("ANANTA_BASE_URL", "")
        or _DEFAULT_BASE_URL
    )


def _login(base: str, user: str, password: str) -> str:
    import requests
    resp = requests.post(
        f"{base}/login",
        json={"username": user, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json().get("data", {}).get("access_token", "")
    if not token:
        raise RuntimeError("No token received")
    return token


def _resolve_task_id(parsed) -> str | None:
    """Return a resolved task ID, showing interactive picker if needed."""
    explicit = getattr(parsed, "task_id_opt", None) or ""
    if explicit:
        return explicit.strip()
    partial = getattr(parsed, "task_id", None) or ""
    if partial and len(partial) == 36 and partial.count("-") == 4:
        return partial  # full UUID

    from agent.cli.utils.interactive import fetch_tasks, resolve_id
    base = _base_url(parsed)
    goal_id = getattr(parsed, "goal_id", "")
    try:
        token = _login(base, parsed.user, parsed.password)
        tasks = fetch_tasks(base, token, goal_id=goal_id, limit=30)
    except Exception:
        tasks = []

    if not partial and not tasks:
        print("Error: No tasks found. Provide a task-id or check the goal.", file=sys.stderr)
        return None

    def _disp(t: dict) -> str:
        return f"{t.get('id', '')[:16]}…  {t.get('status', ''):16}  {t.get('title', '')[:40]}"

    return resolve_id(partial or None, tasks, label="task", display=_disp)


def _cmd_inspect(parsed) -> int:
    tid = _resolve_task_id(parsed)
    if not tid:
        return 2
    from agent.cli.prompt_inspect import run_prompt_command
    parsed.task_id = tid
    parsed.prompt_cmd = "task-inspect"
    return run_prompt_command(parsed)


def _cmd_list(parsed) -> int:
    import requests
    base = _base_url(parsed)
    try:
        token = _login(base, parsed.user, parsed.password)
        params = f"limit={parsed.limit}"
        if parsed.goal_id:
            params += f"&goal_id={parsed.goal_id}"
        r = requests.get(f"{base}/tasks?{params}", headers={"Authorization": f"Bearer {token}"}, timeout=15)
        if not r.ok:
            print(f"Error: {r.status_code}", file=sys.stderr)
            return 1
        tasks = r.json().get("data") or []
        if not isinstance(tasks, list):
            tasks = []
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 4

    if getattr(parsed, "json", False):
        print(json.dumps(tasks, indent=2))
    else:
        print(f"{'TASK ID':<38} {'STATUS':<20} TITLE")
        print("-" * 90)
        for t in tasks:
            print(
                f"{t.get('id', ''):<38} "
                f"{t.get('status', ''):20} "
                f"{t.get('title', '')[:40]}"
            )
        if not tasks:
            print("(no tasks)")
    return 0
