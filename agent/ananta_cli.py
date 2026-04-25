from __future__ import annotations

import sys

from agent import cli_goals
from agent.cli import init_wizard


def _print_help() -> None:
    print("Ananta CLI")
    print("Usage:")
    print("  ananta init [options]")
    print("  ananta goals [agent.cli_goals options]")
    print("")
    print("Examples:")
    print("  ananta init --yes --runtime-mode local-dev --llm-backend ollama")
    print("  ananta goals --status")


def _run_goals(argv: list[str]) -> int:
    previous_argv = list(sys.argv)
    try:
        sys.argv = ["cli_goals", *argv]
        cli_goals.main()
        return 0
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        return 1
    finally:
        sys.argv = previous_argv


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else list(sys.argv[1:])
    if not args or args[0] in {"-h", "--help", "help"}:
        _print_help()
        return 0 if args else 2

    command, *rest = args
    if command == "init":
        return init_wizard.main(rest)
    if command in {"goals", "goal"}:
        return _run_goals(rest)

    print(f"Error: Unknown command '{command}'")
    _print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

