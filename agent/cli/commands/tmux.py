"""ananta tmux — shortcut commands for editing files and launching TUI tools."""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from typing import Callable


SUBCOMMANDS = ["edit", "tool"]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ananta tmux",
        description="Shortcut commands for opening files and TUI tools in the current terminal.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ananta tmux edit README.md\n"
            "  ananta tmux edit app.py --with nvim\n"
            "  ananta tmux tool lazygit\n"
        ),
    )
    sub = p.add_subparsers(dest="tmux_cmd", metavar="<action>")

    edit_p = sub.add_parser("edit", help="Open a file in the resolved editor.")
    edit_p.add_argument("file", help="File path to open.")
    edit_p.add_argument("--with", metavar="EDITOR", dest="with_editor", default=None, help="Override editor (must be in allowed_tools).")
    edit_p.add_argument("--readonly", action="store_true", help="Open in read-only mode where supported.")
    edit_p.add_argument("--workspace", metavar="DIR", default=None, help="Workspace root (default: current directory).")

    tool_p = sub.add_parser("tool", help="Launch a TUI tool profile.")
    tool_p.add_argument("tool_id", help="Tool profile ID (e.g. git_ui, file_manager).")
    tool_p.add_argument("--workspace", metavar="DIR", default=None, help="Workspace root (default: current directory).")

    return p


def dispatch(
    argv: Sequence[str],
    *,
    _exec_fn: Callable[[list[str]], None] | None = None,
) -> int:
    p = _build_parser()
    if not argv or list(argv)[0] in {"-h", "--help"}:
        p.print_help()
        return 0

    try:
        args = p.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    if not args.tmux_cmd:
        p.print_help()
        return 2

    exec_fn = _exec_fn or _default_exec

    if args.tmux_cmd == "edit":
        from agent.cli.commands.tui_editor import _open_file, _resolve_workspace
        workspace = _resolve_workspace(args.workspace)
        return _open_file(
            args.file,
            workspace=workspace,
            with_editor=args.with_editor,
            readonly=args.readonly,
            exec_fn=exec_fn,
        )

    if args.tmux_cmd == "tool":
        from agent.cli.commands.tui_editor import _launch_tool, _resolve_workspace
        workspace = _resolve_workspace(args.workspace)
        return _launch_tool(args.tool_id, workspace=workspace, exec_fn=exec_fn)

    p.print_help()
    return 2


def _default_exec(argv: list[str]) -> None:
    os.execvp(argv[0], argv)
