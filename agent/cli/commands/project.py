"""ananta project — project context management."""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Any

SUBCOMMANDS = ["init", "scan", "context"]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ananta project",
        description="Initialize and scan project context for Ananta goals.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ananta project init\n"
            "  ananta project scan\n"
            "  ananta project context\n"
        ),
    )
    _configure_subparsers(p)
    return p


def _configure_subparsers(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="project_cmd", metavar="<action>")

    init_p = sub.add_parser("init", help="[MUTATING] Initialize project metadata (.ananta/).")
    init_p.add_argument("--path", default=".", help="Project path (default: current directory).")
    init_p.add_argument("--yes", action="store_true", help="Non-interactive mode.")

    scan_p = sub.add_parser("scan", help="Scan project structure and update index.")
    scan_p.add_argument("--path", default=".", help="Project path.")
    scan_p.add_argument("--json", action="store_true")

    ctx_p = sub.add_parser("context", help="Build and show current project context summary.")
    ctx_p.add_argument("--json", action="store_true")
    ctx_p.add_argument("--path", default=".")


def dispatch(argv: Sequence[str]) -> int:
    parser = _build_parser()
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    try:
        parsed = parser.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2
    cmd = parsed.project_cmd
    if cmd in ("init", "scan", "context"):
        print(f"Error: 'ananta project {cmd}' is not yet implemented.", file=sys.stderr)
        return 1
    parser.print_help()
    return 0


def register(subparsers: Any) -> None:
    p = subparsers.add_parser("project", help="Initialize and scan project context.")
    _configure_subparsers(p)
    p.set_defaults(_dispatch=dispatch)
