#!/usr/bin/env python3
"""
Minimales CLI zum Testen von Ananta Goals.

Beispiele:
  # Goal einreichen und auf Ergebnis warten
  python scripts/goal_cli.py run "Create a Fibonacci REST API in Python"

  # Mit OpenCode-Profil
  python scripts/goal_cli.py run "Create a Fibonacci REST API" --profile opencode_preconfigured

  # Status eines Goals prüfen
  python scripts/goal_cli.py status <goal_id>

  # Planning-Config setzen (LMStudio, 700s Timeout)
  python scripts/goal_cli.py setup-planning

  # Aktive Goals anzeigen
  python scripts/goal_cli.py goals
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import requests

DEFAULT_BASE_URL = "http://localhost:5000"
DEFAULT_USER = "admin"
DEFAULT_PASSWORD = "test123"

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "aborted", "timeout"}


def _login(base_url: str, user: str, password: str) -> str:
    resp = requests.post(f"{base_url}/login", json={"username": user, "password": password}, timeout=15)
    resp.raise_for_status()
    token = resp.json().get("data", {}).get("access_token", "")
    if not token:
        raise RuntimeError("Login fehlgeschlagen – kein Token erhalten")
    return token


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def cmd_run(args: argparse.Namespace) -> int:
    base = args.base_url
    token = _login(base, args.user, args.password)

    payload: dict[str, Any] = {
        "goal": args.goal,
        "mode": "new_software_project" if args.software else "generic",
    }
    if args.profile:
        payload["execution_preferences"] = {"config_profile": args.profile}

    resp = requests.post(f"{base}/goals", json=payload, headers=_headers(token), timeout=30)
    if not resp.ok:
        print(f"Fehler beim Einreichen: {resp.status_code} {resp.text[:300]}", file=sys.stderr)
        return 1

    goal_id = resp.json().get("data", {}).get("goal", {}).get("id", "")
    if not goal_id:
        print(f"Keine Goal-ID in Antwort: {resp.text[:300]}", file=sys.stderr)
        return 1

    print(f"Goal eingereicht: {goal_id}")
    print(f"  URL: {base}/goals/{goal_id}")

    if not args.wait:
        return 0

    poll = args.poll
    deadline = time.time() + args.timeout
    last_status = ""
    while time.time() < deadline:
        r = requests.get(f"{base}/goals/{goal_id}", timeout=15)
        if r.ok:
            g = r.json().get("data", {})
            status = g.get("status", "")
            if status != last_status:
                elapsed = int(time.time() - (deadline - args.timeout))
                print(f"  [{elapsed:4d}s] Status: {status}")
                last_status = status
            if status in TERMINAL_STATUSES:
                print(f"\nGoal abgeschlossen: {status}")
                _print_goal_summary(base, goal_id, token)
                return 0 if status == "completed" else 1
        time.sleep(poll)

    print(f"\nTimeout nach {args.timeout}s – letzter Status: {last_status}", file=sys.stderr)
    return 2


def _print_goal_summary(base: str, goal_id: str, token: str) -> None:
    r = requests.get(f"{base}/tasks?goal_id={goal_id}&limit=20", headers=_headers(token), timeout=15)
    if r.ok:
        tasks = r.json().get("data") or []
        if isinstance(tasks, list):
            print(f"\nTasks ({len(tasks)}):")
            for t in tasks:
                print(f"  {t.get('id','')[:16]}  {t.get('status',''):20}  {t.get('title','')[:50]}")


def cmd_status(args: argparse.Namespace) -> int:
    base = args.base_url
    token = _login(base, args.user, args.password)
    r = requests.get(f"{base}/goals/{args.goal_id}", headers=_headers(token), timeout=15)
    if not r.ok:
        print(f"Fehler: {r.status_code}", file=sys.stderr)
        return 1
    g = r.json().get("data", {})
    print(json.dumps({k: g.get(k) for k in ["id", "status", "failure_reason", "created_at"]}, indent=2))
    _print_goal_summary(base, args.goal_id, token)
    return 0


def cmd_goals(args: argparse.Namespace) -> int:
    base = args.base_url
    token = _login(base, args.user, args.password)
    r = requests.get(f"{base}/goals?limit=10", headers=_headers(token), timeout=15)
    if not r.ok:
        print(f"Fehler: {r.status_code}", file=sys.stderr)
        return 1
    goals = r.json().get("data") or []
    if not isinstance(goals, list):
        goals = []
    print(f"{'ID':38} {'STATUS':20} {'CREATED':12}")
    print("-" * 72)
    for g in goals:
        print(f"{g.get('id',''):38} {g.get('status',''):20} {int(g.get('created_at',0))}")
    return 0


def cmd_setup_planning(args: argparse.Namespace) -> int:
    """Setzt die Planning-Policy für LMStudio (700s) + OpenCode Big-Pickle Worker-Config."""
    base = args.base_url
    token = _login(base, args.user, args.password)
    workspace_cfg: dict = {}
    if args.git_workspace:
        workspace_cfg["git_workspace"] = {"enabled": True, "branch_strategy": "goal"}
    if args.artifact_sync:
        workspace_cfg["sync_mode"] = "artifact_hub_sync"
    policy: dict[str, Any] = {
        "default_provider": "lmstudio",
        "default_model": "google/gemma-4-e4b",
        "lmstudio_url": "http://192.168.178.100:1234/v1",
        "planning_policy": {
            "delegated_planning_enabled": False,
            "allowed_planner_roles": ["planning-agent", "planner"],
            "require_review": False,
            "allow_remote_planners": False,
            "max_nodes": 8,
            "max_depth": 8,
            "timeout_seconds": 700,
            "max_output_tokens": 1500,
            "segmented_planning_enabled": False,
            "segment_context_chars": 2400,
            "max_segments": 1,
            "preferred_output_format": "json",
            "selective_repair_rounds": 0,
            "validation_profiles": {},
            "default_runtime_profile": "lmstudio_laptop",
            "runtime_profiles": {
                "lmstudio_laptop": {
                    "timeout_seconds": 700,
                    "max_output_tokens": 1500,
                    "retry_attempts": 1,
                    "retry_backoff_seconds": 1.0,
                    "segmented_planning_enabled": False,
                    "segment_context_chars": 2000,
                    "max_segments": 1,
                    "preferred_output_format": "json",
                }
            },
        },
        **({"workspace": workspace_cfg} if workspace_cfg else {}),
        "worker_runtime": {
            "todo_contract": {
                "planner_llm_enabled": False,
                "planner_llm_retry_attempts": 0,
            },
        },
        "autopilot_task_propose_hard_guard_status": "todo",
    }
    r = requests.post(f"{base}/config", json=policy, headers=_headers(token), timeout=15)
    if not r.ok:
        print(f"Fehler: {r.status_code} {r.text[:200]}", file=sys.stderr)
        return 1
    print("Planning-Policy gesetzt:")
    r2 = requests.get(f"{base}/config", timeout=15)
    if r2.ok:
        data = r2.json().get("data", {})
        pp = data.get("planning_policy", {})
        rp = pp.get("runtime_profiles", {}).get("lmstudio_laptop", {})
        ws = data.get("workspace", {})
        print(f"  timeout_seconds:         {pp.get('timeout_seconds')}")
        print(f"  selective_repair_rounds: {pp.get('selective_repair_rounds')}")
        print(f"  require_review:          {pp.get('require_review')}")
        print(f"  lmstudio_laptop.timeout: {rp.get('timeout_seconds')}")
        git_ws = ws.get("git_workspace", {})
        if git_ws.get("enabled"):
            print(f"  git_workspace.enabled:   True (branch_strategy={git_ws.get('branch_strategy', 'goal')})")
        sync_mode = ws.get("sync_mode", "")
        if sync_mode:
            print(f"  workspace.sync_mode:     {sync_mode}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Ananta Goal CLI")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--user", default=DEFAULT_USER)
    p.add_argument("--password", default=DEFAULT_PASSWORD)

    sub = p.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="Goal einreichen")
    run_p.add_argument("goal", help="Goal-Text")
    run_p.add_argument("--profile", default="opencode_preconfigured", help="Config-Profil")
    run_p.add_argument("--software", action="store_true", default=False, help="Software-Projekt-Modus (new_software_project)")
    run_p.add_argument("--no-wait", dest="wait", action="store_false", default=True)
    run_p.add_argument("--timeout", type=int, default=900, help="Warte-Timeout in Sekunden")
    run_p.add_argument("--poll", type=float, default=10.0, help="Poll-Intervall in Sekunden")

    st_p = sub.add_parser("status", help="Goal-Status abfragen")
    st_p.add_argument("goal_id")

    sub.add_parser("goals", help="Letzte Goals anzeigen")
    sp_p = sub.add_parser("setup-planning", help="Planning-Policy für LMStudio konfigurieren")
    sp_p.add_argument(
        "--git-workspace",
        action="store_true",
        default=False,
        help="Git-Workspace-Sharing zwischen Tasks eines Goals aktivieren",
    )
    sp_p.add_argument(
        "--artifact-sync",
        action="store_true",
        default=False,
        help="Artifact-Hub-Sync aktivieren: Worker laden Artefakte zum Hub hoch, Folge-Tasks materialisieren sie",
    )

    args = p.parse_args()
    if args.cmd == "run":
        return cmd_run(args)
    elif args.cmd == "status":
        return cmd_status(args)
    elif args.cmd == "goals":
        return cmd_goals(args)
    elif args.cmd == "setup-planning":
        return cmd_setup_planning(args)
    else:
        p.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
