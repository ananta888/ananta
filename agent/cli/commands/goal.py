"""ananta goal — user-facing goal operations."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence
from typing import Any

SUBCOMMANDS = ["create", "list", "inspect", "status", "ask", "plan", "review",
               "diagnose", "patch", "repair-admin", "new-project", "evolve-project"]

_DEFAULT_BASE_URL = "http://localhost:5000"
_DEFAULT_USER = "admin"
_DEFAULT_PASSWORD = "test123"
_TERMINAL = {"completed", "failed", "cancelled", "aborted", "timeout"}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ananta goal",
        description=(
            "Create, list, and inspect Ananta goals.\n\n"
            "Shortcut aliases (ask, plan, review, new-project, ...) are preserved as\n"
            "convenience commands that map to 'ananta goal create' with a preset mode."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ananta goal list\n"
            "  ananta goal create \"Build a Fibonacci API\"\n"
            "  ananta goal create \"Build a Fibonacci API\" --profile opencode_preconfigured\n"
            "  ananta goal status <goal-id>\n"
            "  ananta goal inspect <goal-id>\n"
            "  ananta goal ask \"What should I do next?\"\n"
            "  ananta goal new-project \"Create a REST API for users\"\n"
        ),
    )
    _configure_subparsers(p)
    return p


def _configure_subparsers(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="goal_cmd", metavar="<action>")

    # Core commands
    cr_p = sub.add_parser("create", help="[MUTATING] Submit a new goal.")
    cr_p.add_argument("goal_text", help="Goal description text.")
    cr_p.add_argument("--profile", default="opencode_preconfigured", help="Config profile name.")
    cr_p.add_argument("--mode", default="generic", help="Goal mode (generic, new_software_project).")
    cr_p.add_argument("--no-wait", dest="wait", action="store_false", default=True,
                      help="Return immediately without polling for completion.")
    cr_p.add_argument("--timeout", type=int, default=900, help="Max wait time in seconds.")
    cr_p.add_argument("--poll", type=float, default=10.0, help="Poll interval in seconds.")
    cr_p.add_argument("--json", action="store_true")
    cr_p.add_argument("--base-url", default=None)
    cr_p.add_argument("--user", default=_DEFAULT_USER)
    cr_p.add_argument("--password", default=_DEFAULT_PASSWORD)

    li_p = sub.add_parser("list", help="List recent goals.")
    li_p.add_argument("--limit", type=int, default=10)
    li_p.add_argument("--json", action="store_true")
    li_p.add_argument("--base-url", default=None)
    li_p.add_argument("--user", default=_DEFAULT_USER)
    li_p.add_argument("--password", default=_DEFAULT_PASSWORD)

    ins_p = sub.add_parser("inspect", help="Inspect one goal including its tasks.")
    ins_p.add_argument("goal_id", nargs="?", default="",
                       help="Goal ID or prefix. Omit for interactive selection.")
    ins_p.add_argument("--json", action="store_true")
    ins_p.add_argument("--base-url", default=None)
    ins_p.add_argument("--user", default=_DEFAULT_USER)
    ins_p.add_argument("--password", default=_DEFAULT_PASSWORD)

    st_p = sub.add_parser("status", help="Show current status of a goal.")
    st_p.add_argument("goal_id", nargs="?", default="",
                       help="Goal ID or prefix. Omit for interactive selection.")
    st_p.add_argument("--json", action="store_true")
    st_p.add_argument("--base-url", default=None)
    st_p.add_argument("--user", default=_DEFAULT_USER)
    st_p.add_argument("--password", default=_DEFAULT_PASSWORD)

    # Shortcut aliases
    for alias, mode, desc in [
        ("ask", "generic", "[MUTATING] Shortcut: create a question/analysis goal."),
        ("plan", "generic", "[MUTATING] Shortcut: create a planning goal."),
        ("review", "generic", "[MUTATING] Shortcut: create a code review goal."),
        ("diagnose", "generic", "[MUTATING] Shortcut: create a diagnostics goal."),
        ("patch", "generic", "[MUTATING] Shortcut: create a patch/fix goal."),
        ("repair-admin", "generic", "[MUTATING] Shortcut: create an admin repair goal."),
        ("new-project", "new_software_project", "[MUTATING] Shortcut: create a new software project goal."),
        ("evolve-project", "generic", "[MUTATING] Shortcut: evolve an existing project."),
    ]:
        ap = sub.add_parser(alias, help=desc)
        ap.add_argument("goal_text", help="Goal description.")
        ap.add_argument("--profile", default="opencode_preconfigured")
        ap.add_argument("--no-wait", dest="wait", action="store_false", default=True)
        ap.add_argument("--timeout", type=int, default=900)
        ap.add_argument("--poll", type=float, default=10.0)
        ap.add_argument("--json", action="store_true")
        ap.add_argument("--base-url", default=None)
        ap.add_argument("--user", default=_DEFAULT_USER)
        ap.add_argument("--password", default=_DEFAULT_PASSWORD)
        ap.set_defaults(_goal_mode=mode)


def dispatch(argv: Sequence[str]) -> int:
    parser = _build_parser()
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    try:
        parsed = parser.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    cmd = parsed.goal_cmd
    if cmd == "create":
        return _cmd_create(parsed)
    if cmd == "list":
        return _cmd_list(parsed)
    if cmd == "inspect":
        return _cmd_inspect(parsed)
    if cmd == "status":
        return _cmd_status(parsed)
    if cmd in ("ask", "plan", "review", "diagnose", "patch", "repair-admin",
               "new-project", "evolve-project"):
        mode = getattr(parsed, "_goal_mode", "generic")
        parsed.mode = mode
        return _cmd_create(parsed)
    parser.print_help()
    return 0


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "goal",
        help="Create, list, and inspect goals.",
    )
    _configure_subparsers(p)
    p.set_defaults(_dispatch=dispatch)


# ── Shared helpers ─────────────────────────────────────────────────────────────

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
        raise RuntimeError("Login failed — no token received")
    return token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ── Implementations ────────────────────────────────────────────────────────────

def _cmd_create(parsed) -> int:
    import requests
    base = _base_url(parsed)
    goal_text = getattr(parsed, "goal_text", "")
    mode = getattr(parsed, "mode", "generic")
    profile = getattr(parsed, "profile", "opencode_preconfigured")

    try:
        token = _login(base, parsed.user, parsed.password)
    except Exception as exc:
        print(f"Error: Login failed: {exc}", file=sys.stderr)
        return 4

    payload: dict[str, Any] = {
        "goal": goal_text,
        "mode": mode,
        "execution_preferences": {"config_profile": profile},
    }
    resp = requests.post(f"{base}/goals", json=payload, headers=_auth(token), timeout=30)
    if not resp.ok:
        print(f"Error: {resp.status_code} {resp.text[:300]}", file=sys.stderr)
        return 1

    goal_id = resp.json().get("data", {}).get("goal", {}).get("id", "")
    if not goal_id:
        print(f"Error: No goal ID in response: {resp.text[:300]}", file=sys.stderr)
        return 1

    if getattr(parsed, "json", False):
        print(json.dumps({"goal_id": goal_id, "url": f"{base}/goals/{goal_id}"}))
    else:
        print(f"Goal submitted: {goal_id}")
        print(f"  URL: {base}/goals/{goal_id}")

    if not getattr(parsed, "wait", True):
        return 0

    deadline = time.time() + parsed.timeout
    last_status = ""
    started = time.time()
    while time.time() < deadline:
        r = requests.get(f"{base}/goals/{goal_id}", timeout=15)
        if r.ok:
            g = r.json().get("data", {})
            status = g.get("status", "")
            if status != last_status:
                elapsed = int(time.time() - started)
                print(f"  [{elapsed:4d}s] {status}")
                last_status = status
            if status in _TERMINAL:
                print(f"\nGoal finished: {status}")
                _print_tasks(base, goal_id, token)
                return 0 if status == "completed" else 1
        time.sleep(parsed.poll)

    print(f"\nTimeout after {parsed.timeout}s — last status: {last_status}", file=sys.stderr)
    return 2


def _print_tasks(base: str, goal_id: str, token: str) -> None:
    import requests
    r = requests.get(f"{base}/tasks?goal_id={goal_id}&limit=20", headers=_auth(token), timeout=15)
    if r.ok:
        tasks = r.json().get("data") or []
        if isinstance(tasks, list):
            print(f"\nTasks ({len(tasks)}):")
            for t in tasks:
                print(f"  {t.get('id', '')[:16]}  {t.get('status', ''):20}  {t.get('title', '')[:50]}")


def _cmd_list(parsed) -> int:
    import requests
    base = _base_url(parsed)
    try:
        token = _login(base, parsed.user, parsed.password)
        r = requests.get(f"{base}/goals?limit={parsed.limit}", headers=_auth(token), timeout=15)
        if not r.ok:
            print(f"Error: {r.status_code}", file=sys.stderr)
            return 1
        goals = r.json().get("data") or []
        if not isinstance(goals, list):
            goals = []
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 4

    if getattr(parsed, "json", False):
        print(json.dumps(goals, indent=2))
    else:
        print(f"{'GOAL ID':<38} {'STATUS':<20} CREATED")
        print("-" * 72)
        for g in goals:
            print(f"{g.get('id', ''):<38} {g.get('status', ''):20} {int(g.get('created_at', 0))}")
    return 0


def _resolve_goal_id(parsed) -> str | None:
    """Return a resolved goal ID, showing interactive picker if needed."""
    partial = getattr(parsed, "goal_id", None) or ""
    if partial and len(partial) == 36 and partial.count("-") == 4:
        return partial  # looks like a full UUID, skip fetch

    from agent.cli.utils.interactive import fetch_goals, resolve_id
    base = _base_url(parsed)
    try:
        token = _login(base, parsed.user, parsed.password)
        goals = fetch_goals(base, token, limit=20)
    except Exception:
        goals = []

    if not partial and not goals:
        print("Error: No goals found. Provide --goal-id or create a goal first.", file=sys.stderr)
        return None

    def _disp(g: dict) -> str:
        ts = g.get("created_at", 0)
        return f"{g.get('id', '')[:16]}…  {g.get('status', ''):16}  t={int(ts or 0)}"

    return resolve_id(partial or None, goals, label="goal", display=_disp)


def _cmd_inspect(parsed) -> int:
    import requests
    base = _base_url(parsed)
    gid = _resolve_goal_id(parsed)
    if not gid:
        return 2
    try:
        token = _login(base, parsed.user, parsed.password)
        r = requests.get(f"{base}/goals/{gid}", headers=_auth(token), timeout=15)
        if not r.ok:
            print(f"Error: {r.status_code}", file=sys.stderr)
            return 1
        g = r.json().get("data", {})
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 4

    if getattr(parsed, "json", False):
        print(json.dumps(g, indent=2))
    else:
        print(json.dumps(
            {k: g.get(k) for k in ["id", "status", "failure_reason", "created_at"]},
            indent=2,
        ))
        _print_tasks(base, gid, token)
    return 0


def _cmd_status(parsed) -> int:
    import requests
    base = _base_url(parsed)
    gid = _resolve_goal_id(parsed)
    if not gid:
        return 2
    try:
        token = _login(base, parsed.user, parsed.password)
        r = requests.get(f"{base}/goals/{gid}", headers=_auth(token), timeout=15)
        if not r.ok:
            print(f"Error: {r.status_code}", file=sys.stderr)
            return 1
        g = r.json().get("data", {})
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 4

    if getattr(parsed, "json", False):
        print(json.dumps({"id": gid, "status": g.get("status")}, indent=2))
    else:
        print(f"Goal:   {gid}")
        print(f"Status: {g.get('status', '—')}")
        fr = g.get("failure_reason", "")
        if fr:
            print(f"Reason: {fr}")
    return 0
