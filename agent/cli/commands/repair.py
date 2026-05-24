"""ananta repair — repair procedure commands."""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Any

SUBCOMMANDS = ["analyze", "propose", "run", "verify"]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ananta repair",
        description="Run Ananta deterministic repair procedures.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ananta repair analyze\n"
            "  ananta repair propose\n"
            "  ananta repair run --procedure <name>\n"
            "  ananta repair verify\n"
        ),
    )
    _configure_subparsers(p)
    return p


def _configure_subparsers(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="repair_cmd", metavar="<action>")

    sub.add_parser("analyze", help="Run deterministic analysis on failed state.")

    prop_p = sub.add_parser("propose", help="Propose a repair plan based on analysis.")
    prop_p.add_argument("--json", action="store_true")

    run_p = sub.add_parser("run", help="[MUTATING] Execute a repair procedure.")
    run_p.add_argument("--procedure", default="", help="Procedure name.")
    run_p.add_argument("--dry-run", action="store_true", help="Simulate without mutating state.")

    sub.add_parser("verify", help="Verify repair result and confirm resolution.")


def dispatch(argv: Sequence[str]) -> int:
    parser = _build_parser()
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    try:
        parsed = parser.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2
    cmd = parsed.repair_cmd
    if cmd in ("analyze", "propose", "run", "verify"):
        print(f"Error: 'ananta repair {cmd}' is not yet implemented.", file=sys.stderr)
        print("       Use 'ananta repair-admin <goal>' or the Hub API directly.", file=sys.stderr)
        return 1
    parser.print_help()
    return 0


def register(subparsers: Any) -> None:
    p = subparsers.add_parser("repair", help="Run deterministic repair procedures.")
    _configure_subparsers(p)
    p.set_defaults(_dispatch=dispatch)
