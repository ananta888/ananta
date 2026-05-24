"""ananta worker — worker registration and status commands."""
from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from typing import Any

SUBCOMMANDS = ["list", "register", "start", "status", "logs"]

_DEFAULT_BASE_URL = "http://localhost:5000"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ananta worker",
        description="List and manage Ananta worker agents.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ananta worker list\n"
            "  ananta worker list --json\n"
            "  ananta worker status --worker-id <id>\n"
        ),
    )
    _configure_subparsers(p)
    return p


def _configure_subparsers(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="worker_cmd", metavar="<action>")

    list_p = sub.add_parser("list", help="List registered workers and their status.")
    list_p.add_argument("--json", action="store_true")
    list_p.add_argument("--base-url", default=None)

    reg_p = sub.add_parser("register", help="[MUTATING] Register a new worker with the hub.")
    reg_p.add_argument("--name", required=True, help="Worker name.")
    reg_p.add_argument("--base-url", default=None)

    sub.add_parser("start", help="[MUTATING] Start a worker container.")

    st_p = sub.add_parser("status", help="Show status of a specific worker.")
    st_p.add_argument("--worker-id", dest="worker_id", default="", help="Worker ID.")
    st_p.add_argument("--json", action="store_true")
    st_p.add_argument("--base-url", default=None)

    log_p = sub.add_parser("logs", help="Show recent worker logs.")
    log_p.add_argument("--worker-id", dest="worker_id", default="", help="Worker ID.")
    log_p.add_argument("--lines", type=int, default=50)


def dispatch(argv: Sequence[str]) -> int:
    parser = _build_parser()
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    try:
        parsed = parser.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    cmd = parsed.worker_cmd
    if cmd == "list":
        return _cmd_list(parsed)
    if cmd == "status":
        return _cmd_status(parsed)
    if cmd in ("register", "start", "logs"):
        return _cmd_not_implemented(f"worker {cmd}")
    parser.print_help()
    return 0


def register(subparsers: Any) -> None:
    p = subparsers.add_parser("worker", help="List and manage worker agents.")
    _configure_subparsers(p)
    p.set_defaults(_dispatch=dispatch)


# ── Implementations ────────────────────────────────────────────────────────────

def _cmd_not_implemented(name: str) -> int:
    import sys
    print(f"Error: 'ananta {name}' is not yet implemented.", file=sys.stderr)
    return 1


def _base_url(parsed) -> str:
    return (
        getattr(parsed, "base_url", None)
        or os.environ.get("ANANTA_BASE_URL", "")
        or _DEFAULT_BASE_URL
    )


def _cmd_list(parsed) -> int:
    import sys
    base = _base_url(parsed)
    try:
        import requests
        r = requests.get(f"{base}/agents", timeout=10)
        if not r.ok:
            print(f"Error: {r.status_code}", file=sys.stderr)
            return 1
        agents = r.json().get("data") or []
        if not isinstance(agents, list):
            agents = []
    except Exception as exc:
        print(f"Error: Could not reach hub at {base}: {exc}", file=sys.stderr)
        return 4
    if getattr(parsed, "json", False):
        print(json.dumps(agents, indent=2))
    else:
        print(f"{'WORKER ID':<38} {'NAME':<25} STATUS")
        print("-" * 72)
        for a in agents:
            print(
                f"{a.get('id', ''):<38} "
                f"{a.get('name', ''):<25} "
                f"{a.get('status', '')}"
            )
        if not agents:
            print("(no workers registered)")
    return 0


def _cmd_status(parsed) -> int:
    import sys
    base = _base_url(parsed)
    wid = getattr(parsed, "worker_id", "")
    if not wid:
        print("Error: --worker-id is required.", file=sys.stderr)
        return 2
    try:
        import requests
        r = requests.get(f"{base}/agents/{wid}", timeout=10)
        if not r.ok:
            print(f"Error: {r.status_code}", file=sys.stderr)
            return 1
        data = r.json().get("data", {})
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 4
    if getattr(parsed, "json", False):
        print(json.dumps(data, indent=2))
    else:
        print(f"Worker: {data.get('id', wid)}")
        print(f"Name:   {data.get('name', '—')}")
        print(f"Status: {data.get('status', '—')}")
    return 0
