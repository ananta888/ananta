"""ananta rag — RAG index and retrieval commands."""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Any

SUBCOMMANDS = ["index", "query", "explain", "policy-check"]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ananta rag",
        description="Manage and query the Ananta RAG (retrieval-augmented generation) index.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ananta rag index\n"
            "  ananta rag query \"What does the auth service do?\"\n"
            "  ananta rag explain --query \"auth\"\n"
            "  ananta rag policy-check\n"
        ),
    )
    _configure_subparsers(p)
    return p


def _configure_subparsers(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="rag_cmd", metavar="<action>")

    idx_p = sub.add_parser("index", help="[MUTATING] Build or update the RAG index.")
    idx_p.add_argument("--path", default=".", help="Root path to index.")
    idx_p.add_argument("--force", action="store_true", help="Force full re-index.")

    qry_p = sub.add_parser("query", help="Query the RAG index.")
    qry_p.add_argument("query", help="Query string.")
    qry_p.add_argument("--limit", type=int, default=5)
    qry_p.add_argument("--json", action="store_true")

    exp_p = sub.add_parser("explain", help="Explain why context was selected for a query.")
    exp_p.add_argument("--query", required=True)
    exp_p.add_argument("--json", action="store_true")

    sub.add_parser("policy-check", help="Check context access policy boundaries.")


def dispatch(argv: Sequence[str]) -> int:
    parser = _build_parser()
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    try:
        parsed = parser.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2
    cmd = parsed.rag_cmd
    if cmd in ("index", "query", "explain", "policy-check"):
        print(f"Error: 'ananta rag {cmd}' is not yet implemented.", file=sys.stderr)
        return 1
    parser.print_help()
    return 0


def register(subparsers: Any) -> None:
    p = subparsers.add_parser("rag", help="Manage and query the RAG index.")
    _configure_subparsers(p)
    p.set_defaults(_dispatch=dispatch)
