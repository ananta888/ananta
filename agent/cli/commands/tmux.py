"""ananta tmux — terminal session management and local editor/TUI shortcuts."""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from typing import Callable


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ananta tmux",
        description="Terminal session management and local editor/TUI shortcuts.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Terminal session commands:\n"
            "  ananta tmux targets\n"
            "  ananta tmux start --target-type worker --target-id <id>\n"
            "  ananta tmux start --target-type hub --reason <reason>\n"
            "  ananta tmux attach <session_id>\n"
            "  ananta tmux list\n"
            "  ananta tmux kill <session_id>\n\n"
            "Local editor/TUI shortcuts:\n"
            "  ananta tmux edit README.md\n"
            "  ananta tmux tool lazygit\n"
        ),
    )
    sub = p.add_subparsers(dest="tmux_cmd", metavar="<action>")

    # ── terminal session commands ──────────────────────────────────────────────
    sub.add_parser("targets", help="List terminal-capable targets (worker, hub, hub_as_worker).")

    start_p = sub.add_parser("start", help="Create and open a new terminal session.")
    start_p.add_argument("--target-type", choices=["worker", "hub", "hub_as_worker"], default="worker")
    start_p.add_argument("--target-id", default="")
    start_p.add_argument("--workspace", default=None)
    start_p.add_argument("--goal-id", default=None)
    start_p.add_argument("--task-id", default=None)
    start_p.add_argument("--read-only", action="store_true")
    start_p.add_argument("--reason", default="", help="Required when --target-type=hub.")

    attach_p = sub.add_parser("attach", help="Attach to an existing terminal session.")
    attach_p.add_argument("session_id", help="Session ID to attach.")

    sub.add_parser("list", help="List active terminal sessions.")

    kill_p = sub.add_parser("kill", help="Kill a terminal session.")
    kill_p.add_argument("session_id", help="Session ID to kill.")

    # ── local editor shortcuts ─────────────────────────────────────────────────
    edit_p = sub.add_parser("edit", help="Open a file in the resolved editor.")
    edit_p.add_argument("file", help="File path to open.")
    edit_p.add_argument("--with", metavar="EDITOR", dest="with_editor", default=None)
    edit_p.add_argument("--readonly", action="store_true")
    edit_p.add_argument("--workspace", metavar="DIR", dest="edit_workspace", default=None)

    tool_p = sub.add_parser("tool", help="Launch a TUI tool profile.")
    tool_p.add_argument("tool_id", help="Tool profile ID (e.g. git_ui, file_manager).")
    tool_p.add_argument("--workspace", metavar="DIR", default=None)

    return p


def _api_client():
    from agent.cli.api_client import get_api_client
    return get_api_client()


def _cmd_targets(args: argparse.Namespace) -> int:
    try:
        client = _api_client()
        data = client.get("/terminal/targets").get("data", {})
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    targets = data.get("targets") or []
    if not targets:
        print("no terminal-capable targets available")
        return 0

    print(f"{'TYPE':<18} {'ID':<28} {'DISPLAY NAME':<24} RISK")
    print("-" * 80)
    for t in targets:
        ttype = t.get("target_type", "?")
        tid = t.get("target_id", "?")
        display = t.get("display_name") or t.get("target_id") or "?"
        risk = t.get("risk_class") or "-"
        marker = " [HIGH RISK]" if ttype in {"hub", "hub_as_worker"} else ""
        print(f"{ttype:<18} {tid:<28} {display:<24} {risk}{marker}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    try:
        client = _api_client()
        data = client.get("/terminal/sessions").get("data", {})
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    sessions = data.get("sessions") or []
    if not sessions:
        print("no active terminal sessions")
        return 0

    print(f"{'ID':<38} {'TYPE':<16} {'STATUS':<12} {'TARGET'}")
    print("-" * 90)
    for s in sessions:
        sid = s.get("id", "?")
        stype = s.get("target_type", "?")
        status = s.get("status", "?")
        target = s.get("target_id", "?")
        print(f"{sid:<38} {stype:<16} {status:<12} {target}")
    return 0


def _cmd_start(args: argparse.Namespace) -> int:
    target_type = args.target_type
    target_id = args.target_id

    if target_type in {"hub", "hub_as_worker"}:
        if not args.reason:
            print(
                f"error: --reason is required when --target-type={target_type}\n"
                f"       Hub and Hub-as-Worker terminal access is high risk.",
                file=sys.stderr,
            )
            return 2
        print(f"WARNING: opening {target_type} terminal (high risk) — reason: {args.reason}")

    if not target_id and target_type == "worker":
        try:
            client = _api_client()
            tdata = client.get("/terminal/targets").get("data", {})
            workers = [t for t in (tdata.get("targets") or []) if t.get("target_type") == "worker"]
            if len(workers) == 1:
                target_id = workers[0]["target_id"]
                print(f"using sole worker target: {target_id}")
            elif len(workers) == 0:
                print("error: no worker targets available", file=sys.stderr)
                return 1
            else:
                ids = [t["target_id"] for t in workers]
                print(f"error: multiple workers — specify --target-id: {ids}", file=sys.stderr)
                return 2
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    if not target_id:
        print("error: --target-id is required", file=sys.stderr)
        return 2

    payload: dict = {
        "target_type": target_type,
        "target_id": target_id,
        "read_only": args.read_only,
    }
    if args.workspace:
        payload["workspace_path"] = args.workspace
    if args.goal_id:
        payload["goal_id"] = args.goal_id
    if args.task_id:
        payload["task_id"] = args.task_id

    try:
        client = _api_client()
        result = client.post("/terminal/sessions", json=payload)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not result.get("data", {}).get("ok"):
        rc = result.get("data", {}).get("reason_code") or result.get("message") or "error"
        print(f"error: {rc}", file=sys.stderr)
        return 1

    session_obj = result["data"]["session"]
    session_id = session_obj.get("id")
    tmux_name = session_obj.get("tmux_session_name")
    print(f"session created: {session_id}")
    print(f"tmux session:    {tmux_name}")

    if tmux_name:
        print(f"\nattaching: tmux attach-session -t {tmux_name}")
        os.execvp("tmux", ["tmux", "attach-session", "-t", tmux_name])

    return 0


def _cmd_attach(args: argparse.Namespace) -> int:
    session_id = args.session_id
    try:
        client = _api_client()
        result = client.get(f"/terminal/sessions/{session_id}")
        sess = result.get("data", {}).get("session")
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not sess:
        print(f"error: session {session_id} not found", file=sys.stderr)
        return 1

    tmux_name = sess.get("tmux_session_name")
    if not tmux_name:
        print("error: no tmux session name on record — session may have ended", file=sys.stderr)
        return 1

    if sess.get("target_type") in {"hub", "hub_as_worker"}:
        print(f"WARNING: attaching to HIGH RISK {sess['target_type']} terminal session")

    print(f"attaching: tmux attach-session -t {tmux_name}")
    os.execvp("tmux", ["tmux", "attach-session", "-t", tmux_name])
    return 0


def _cmd_kill(args: argparse.Namespace) -> int:
    session_id = args.session_id
    try:
        client = _api_client()
        result = client.delete(f"/terminal/sessions/{session_id}")
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not result.get("data", {}).get("ok"):
        rc = result.get("data", {}).get("reason_code") or result.get("message") or "error"
        print(f"error: {rc}", file=sys.stderr)
        return 1

    print(f"session {session_id} killed")
    return 0


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

    if args.tmux_cmd == "targets":
        return _cmd_targets(args)
    if args.tmux_cmd == "list":
        return _cmd_list(args)
    if args.tmux_cmd == "start":
        return _cmd_start(args)
    if args.tmux_cmd == "attach":
        return _cmd_attach(args)
    if args.tmux_cmd == "kill":
        return _cmd_kill(args)

    exec_fn = _exec_fn or _default_exec

    if args.tmux_cmd == "edit":
        from agent.cli.commands.tui_editor import _open_file, _resolve_workspace
        workspace = _resolve_workspace(getattr(args, "edit_workspace", None))
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
