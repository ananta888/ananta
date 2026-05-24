"""ananta tui --open / --tool — embedded editor and TUI tool launcher."""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from typing import Callable


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ananta tui",
        description="Open a file in the configured editor or launch a TUI tool.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ananta tui --open README.md\n"
            "  ananta tui --open app.py --with nvim\n"
            "  ananta tui --open config.json --readonly\n"
            "  ananta tui --tool lazygit\n"
        ),
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--open", metavar="FILE", dest="open_file", help="File to open in the resolved editor.")
    group.add_argument("--tool", metavar="TOOL_ID", dest="tool_id", help="TUI tool profile to launch (e.g. git_ui, file_manager).")
    p.add_argument("--with", metavar="EDITOR", dest="with_editor", default=None, help="Override editor for this open action (must be in allowed_tools).")
    p.add_argument("--readonly", action="store_true", help="Open file in read-only mode where supported.")
    p.add_argument("--workspace", metavar="DIR", default=None, help="Workspace root (default: current directory).")
    p.add_argument("--target", metavar="TYPE", default="worker", choices=["worker", "hub", "hub_as_worker"], help="Target type (default: worker).")
    return p


def _resolve_workspace(workspace: str | None) -> str:
    return os.path.abspath(workspace or os.getcwd())


def dispatch(
    argv: Sequence[str],
    *,
    _exec_fn: Callable[[list[str]], None] | None = None,
) -> int:
    """Dispatch ananta tui --open / --tool.

    _exec_fn: injectable for testing (replaces os.execvp). Receives final argv list.
    """
    p = _build_parser()
    if not argv or list(argv)[0] in {"-h", "--help"}:
        p.print_help()
        return 0

    try:
        args = p.parse_args(list(argv))
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    workspace = _resolve_workspace(args.workspace)
    exec_fn = _exec_fn or _default_exec

    if args.open_file:
        return _open_file(
            args.open_file,
            workspace=workspace,
            with_editor=args.with_editor,
            readonly=args.readonly,
            exec_fn=exec_fn,
        )

    if args.tool_id:
        return _launch_tool(args.tool_id, workspace=workspace, exec_fn=exec_fn)

    p.print_help()
    return 2


def _open_file(
    file_path: str,
    *,
    workspace: str,
    with_editor: str | None,
    readonly: bool,
    exec_fn: Callable[[list[str]], None],
) -> int:
    from agent.services.workspace_path_validator import WorkspacePathValidator
    from agent.services.editor_resolver import get_editor_resolver

    validator = WorkspacePathValidator(workspace)
    path_result = validator.validate(file_path)
    if not path_result.ok:
        print(f"Error: {path_result.reason}: {file_path!r}", file=sys.stderr)
        return 2

    resolver = get_editor_resolver()
    resolution = resolver.resolve(path_result.resolved_path, with_editor=with_editor)

    if with_editor and resolution.editor_id != with_editor:
        print(f"Warning: editor {with_editor!r} not in allowed_tools — using {resolution.editor_id!r}", file=sys.stderr)

    argv = resolution.build_argv(path_result.resolved_path, readonly=readonly)
    exec_fn(argv)
    return 0


def _launch_tool(
    tool_id: str,
    *,
    workspace: str,
    exec_fn: Callable[[list[str]], None],
) -> int:
    from agent.services.tui_tool_registry import get_tui_tool_registry

    registry = get_tui_tool_registry()
    profile = registry.get_tool_profile(tool_id)
    if profile is None:
        known = ", ".join(registry.list_allowed_tools())
        print(f"Error: unknown tool {tool_id!r}. Known tools: {known}", file=sys.stderr)
        return 2

    argv_args = [a.replace("{workspace}", workspace) for a in profile.args_template]
    argv = [profile.command] + argv_args
    exec_fn(argv)
    return 0


def _default_exec(argv: list[str]) -> None:
    os.execvp(argv[0], argv)  # replaces current process — terminal takes over
