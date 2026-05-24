"""ananta llm — LLM backend and log commands."""
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any

SUBCOMMANDS = ["list", "test", "benchmark", "log"]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ananta llm",
        description="Inspect LLM backends and view invocation logs.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ananta llm list\n"
            "  ananta llm log tail\n"
            "  ananta llm log tail --limit 5 --json\n"
            "  ananta llm test --provider lmstudio\n"
            "  ananta llm benchmark\n"
        ),
    )
    _configure_subparsers(p)
    return p


def _configure_subparsers(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="llm_cmd", metavar="<action>")

    list_p = sub.add_parser("list", help="List configured LLM backends and their status.")
    list_p.add_argument("--json", action="store_true")

    test_p = sub.add_parser("test", help="Send a test prompt to a backend and report result.")
    test_p.add_argument("--provider", default="", help="Provider name (lmstudio, ollama, openai-compatible).")
    test_p.add_argument("--model", default="", help="Model identifier.")
    test_p.add_argument("--json", action="store_true")

    bench_p = sub.add_parser("benchmark", help="Run a small deterministic benchmark against a backend.")
    bench_p.add_argument("--provider", default="")
    bench_p.add_argument("--model", default="")
    bench_p.add_argument("--json", action="store_true")

    log_p = sub.add_parser("log", help="Show LLM invocation logs (replaces ananta llm-log).")
    log_sub = log_p.add_subparsers(dest="log_cmd", metavar="<log-action>")
    tail_p = log_sub.add_parser("tail", help="Show recent LLM requests.")
    tail_p.add_argument("--limit", type=int, default=20, help="Number of entries to show.")
    tail_p.add_argument("--provider", default="", help="Filter by provider.")
    tail_p.add_argument("--model", default="", help="Filter by model.")
    tail_p.add_argument("--goal-id", dest="goal_id", default="", help="Filter by goal ID.")
    tail_p.add_argument("--task-id", dest="task_id", default="", help="Filter by task ID.")
    tail_p.add_argument("--json", action="store_true")


def dispatch(argv: Sequence[str]) -> int:
    parser = _build_parser()
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    try:
        parsed = parser.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    cmd = parsed.llm_cmd
    if cmd == "list":
        return _cmd_list(parsed)
    if cmd == "test":
        return _cmd_not_implemented("llm test")
    if cmd == "benchmark":
        return _cmd_not_implemented("llm benchmark")
    if cmd == "log":
        return _cmd_log(parsed)
    parser.print_help()
    return 0


def register(subparsers: Any) -> None:
    p = subparsers.add_parser("llm", help="LLM backend inspection and log viewer.")
    _configure_subparsers(p)
    p.set_defaults(_dispatch=dispatch)


# ── Implementations ────────────────────────────────────────────────────────────

def _cmd_not_implemented(name: str) -> int:
    import sys
    print(f"Error: '{name}' is not yet implemented.", file=sys.stderr)
    print(f"       See 'ananta {name.split()[0]} --help' for available commands.", file=sys.stderr)
    return 1


def _cmd_list(parsed) -> int:
    import os
    providers = []
    lmstudio_url = os.environ.get("LMSTUDIO_URL", "http://192.168.178.100:1234/v1")
    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")

    def _probe(name: str, url: str) -> dict:
        try:
            import requests
            r = requests.get(url, timeout=4)
            ok = r.status_code < 500
        except Exception:
            ok = False
        return {"name": name, "url": url, "reachable": ok}

    providers = [
        _probe("lmstudio", lmstudio_url),
        _probe("ollama", ollama_url),
    ]
    if getattr(parsed, "json", False):
        print(json.dumps(providers, indent=2))
    else:
        print(f"{'PROVIDER':<20} {'URL':<40} REACHABLE")
        print("-" * 72)
        for p in providers:
            status = "yes" if p["reachable"] else "no"
            print(f"{p['name']:<20} {p['url']:<40} {status}")
    return 0


def _cmd_log(parsed) -> int:
    log_cmd = getattr(parsed, "log_cmd", None)
    if log_cmd == "tail" or log_cmd is None:
        return _cmd_log_tail(parsed)
    import sys
    print("Use: ananta llm log tail [options]", file=sys.stderr)
    return 2


def _cmd_log_tail(parsed) -> int:
    from agent.cli.prompt_inspect import run_llm_log_command
    parsed.llm_log_cmd = "tail"
    return run_llm_log_command(parsed)


# Legacy entry-point used by main.py flat dispatch (ananta llm-log)
def dispatch_llm_log(argv: Sequence[str]) -> int:
    """Dispatch the legacy 'ananta llm-log' flat command."""
    import argparse as _ap
    sub_parser = _ap.ArgumentParser(prog="ananta llm-log")
    log_sub = sub_parser.add_subparsers(dest="llm_log_cmd")
    tail_p = log_sub.add_parser("tail", help="Show recent LLM requests")
    tail_p.add_argument("--limit", type=int, default=20)
    tail_p.add_argument("--provider")
    tail_p.add_argument("--model")
    tail_p.add_argument("--goal-id", dest="goal_id")
    tail_p.add_argument("--task-id", dest="task_id")
    tail_p.add_argument("--json", action="store_true")
    if not argv or argv[0] in ("-h", "--help"):
        sub_parser.print_help()
        return 0
    try:
        parsed = sub_parser.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2
    from agent.cli.prompt_inspect import run_llm_log_command
    return run_llm_log_command(parsed)
