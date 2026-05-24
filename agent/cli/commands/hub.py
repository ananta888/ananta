"""ananta hub — hub lifecycle commands."""
from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from typing import Any

SUBCOMMANDS = ["start", "status", "stop", "logs"]

_DEFAULT_BASE_URL = "http://localhost:5000"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ananta hub",
        description="Manage the Ananta hub process.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ananta hub status\n"
            "  ananta hub status --json\n"
            "  ananta hub logs\n"
        ),
    )
    _configure_subparsers(p)
    return p


def _configure_subparsers(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="hub_cmd", metavar="<action>")

    st_p = sub.add_parser("status", help="Show hub health and version.")
    st_p.add_argument("--json", action="store_true")
    st_p.add_argument("--base-url", default=None, help=f"Hub URL (default: {_DEFAULT_BASE_URL}).")

    sub.add_parser("start", help="[MUTATING] Start the hub runtime (via Docker Compose).")
    sub.add_parser("stop", help="[MUTATING] Stop the hub runtime.")

    log_p = sub.add_parser("logs", help="Stream hub logs.")
    log_p.add_argument("--lines", type=int, default=50, help="Number of recent log lines.")
    log_p.add_argument("--follow", "-f", action="store_true", help="Follow log output.")


def dispatch(argv: Sequence[str]) -> int:
    parser = _build_parser()
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    try:
        parsed = parser.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    cmd = parsed.hub_cmd
    if cmd == "status":
        return _cmd_status(parsed)
    if cmd in ("start", "stop", "logs"):
        return _cmd_not_implemented(f"hub {cmd}")
    parser.print_help()
    return 0


def register(subparsers: Any) -> None:
    p = subparsers.add_parser("hub", help="Manage the Ananta hub process.")
    _configure_subparsers(p)
    p.set_defaults(_dispatch=dispatch)


# ── Implementations ────────────────────────────────────────────────────────────

def _cmd_not_implemented(name: str) -> int:
    import sys
    print(f"Error: 'ananta {name}' is not yet implemented.", file=sys.stderr)
    print(f"       Use Docker Compose directly: docker compose up/down", file=sys.stderr)
    return 1


def _cmd_status(parsed) -> int:
    import sys
    base = (
        getattr(parsed, "base_url", None)
        or os.environ.get("ANANTA_BASE_URL", "")
        or _DEFAULT_BASE_URL
    )
    try:
        import requests
        r = requests.get(f"{base}/health", timeout=8)
        ok = r.status_code == 200
        data = r.json() if ok else {}
    except Exception as exc:
        if getattr(parsed, "json", False):
            print(json.dumps({"reachable": False, "url": base, "error": str(exc)}))
        else:
            print(f"Hub unreachable at {base}: {exc}", file=sys.stderr)
        return 4

    if getattr(parsed, "json", False):
        print(json.dumps({"reachable": True, "url": base, "data": data}, indent=2))
    else:
        print(f"Hub:      {base}")
        print(f"Status:   {'ok' if ok else 'unreachable'}")
        if data:
            print(f"Version:  {data.get('version', '—')}")
    return 0
