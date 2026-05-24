from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

COMPACT_HEADER_LINES = 8


@dataclass(frozen=True)
class StatusSnapshot:
    tasks_queued: int = 0
    tasks_running: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    workers_connected: int = 0
    uptime_seconds: float = 0.0
    mode: str = "dashboard"
    goal_active: str = ""
    cwd: str = ""
    repo_root: str = ""
    git_branch: str = ""
    git_dirty: bool = False
    git_user: str = ""
    endpoint: str = ""
    auth_state: str = ""
    section: str = ""
    timestamp: float = field(default_factory=time.time)


def collect_status(
    *,
    mode: str = "dashboard",
    goal_active: str = "",
    endpoint: str = "",
    auth_state: str = "",
    section: str = "",
    tasks_queued: int = 0,
    tasks_running: int = 0,
    tasks_completed: int = 0,
    tasks_failed: int = 0,
    workers_connected: int = 0,
    uptime_seconds: float = 0.0,
) -> StatusSnapshot:
    cwd = _safe_cwd()
    repo_root, git_branch, git_dirty, git_user = _git_info(cwd)
    return StatusSnapshot(
        tasks_queued=tasks_queued,
        tasks_running=tasks_running,
        tasks_completed=tasks_completed,
        tasks_failed=tasks_failed,
        workers_connected=workers_connected,
        uptime_seconds=uptime_seconds,
        mode=mode,
        goal_active=goal_active,
        cwd=cwd,
        repo_root=repo_root,
        git_branch=git_branch,
        git_dirty=git_dirty,
        git_user=git_user,
        endpoint=endpoint,
        auth_state=auth_state,
        section=section,
    )


def _safe_cwd() -> str:
    try:
        return os.getcwd()
    except (OSError, PermissionError):
        return ""


def _git_info(cwd: str) -> tuple[str, str, bool, str]:
    if not cwd:
        return "", "", False, ""
    try:
        import subprocess
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2, cwd=cwd,
        ).stdout.strip()
        if not root:
            return "", "", False, ""
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2, cwd=cwd,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=2, cwd=cwd,
        ).stdout.strip()
        user = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True, text=True, timeout=2, cwd=cwd,
        ).stdout.strip()
        return root, branch, bool(dirty), user
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "", "", False, ""


def _shorten_path(path: str, max_len: int = 32) -> str:
    if len(path) <= max_len:
        return path
    mid = max_len // 2 - 2
    return path[:mid] + "..." + path[-(max_len - mid - 3):]


def _repo_relative_path(cwd: str, repo_root: str) -> str:
    if not cwd or not repo_root:
        return cwd
    try:
        rel = Path(cwd).relative_to(Path(repo_root))
        parts = rel.parts
        if len(parts) <= 2:
            return str(rel)
        return str(Path(parts[0]) / "..." / parts[-1])
    except ValueError:
        return cwd


def _format_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}h{m:02d}m{s:02d}s"
    if m > 0:
        return f"{m}m{s:02d}s"
    return f"{s}s"


_STATUS_TEMPLATE_RIGHT = """\
{uptime:>14}
{workers:>14}
{queued:>14}
{running:>14}
{completed:>14}
{failed:>14}
{mode:>14}
{goal:>14}"""


def format_status_lines(
    snapshot: StatusSnapshot,
    *,
    color: bool = True,
    width: int = 80,
) -> list[str]:
    dim   = "\x1b[2m"    if color else ""
    bold  = "\x1b[1m"    if color else ""
    green = "\x1b[32m"   if color else ""
    red   = "\x1b[31m"   if color else ""
    rst   = "\x1b[0m"    if color else ""

    max_val = max(10, width - 12)

    def kv(label: str, value: str, val_style: str = "") -> str:
        return f"{dim}{label:<9}{rst}{val_style}{value}{rst}"

    # \u2500\u2500 cwd \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    cwd_display = _repo_relative_path(snapshot.cwd, snapshot.repo_root)
    if not cwd_display:
        cwd_display = _shorten_path(snapshot.cwd or "?", max_val)
    else:
        cwd_display = _shorten_path(cwd_display, max_val)

    # Show repo name prominently if inside a git repo
    repo_name = ""
    if snapshot.repo_root:
        repo_name = Path(snapshot.repo_root).name

    # \u2500\u2500 git \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    branch_str = snapshot.git_branch or ""
    dirty_mark = f" {red}\u2717{rst}" if snapshot.git_dirty else (f" {green}\u2713{rst}" if branch_str else "")
    git_line   = ""
    if branch_str:
        git_line = f"{bold}{branch_str}{rst}{dirty_mark}"
    if snapshot.git_user and branch_str:
        git_line = f"{snapshot.git_user}  {bold}{branch_str}{rst}{dirty_mark}"
    elif snapshot.git_user:
        git_line = snapshot.git_user

    # \u2500\u2500 endpoint / connections \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    ep = (snapshot.endpoint or "").replace("http://", "").replace("https://", "")
    ep = _shorten_path(ep or "\u2013", max_val)

    auth_ok = snapshot.auth_state in ("token", "session_env")
    auth_str = f"{green}{snapshot.auth_state}{rst}" if auth_ok else f"{red}{snapshot.auth_state or '?'}{rst}"
    if color:
        conn_dot = f"{green}\u25cf{rst}" if auth_ok else f"{red}\u25cf{rst}"
    else:
        conn_dot = "*" if auth_ok else "x"

    workers = snapshot.workers_connected
    wrk_str = f"{workers} worker{'s' if workers != 1 else ''}" if workers else "no workers"

    lines: list[str] = []

    if repo_name:
        lines.append(kv("repo", f"{bold}{repo_name}{rst}"))
    lines.append(kv("cwd", cwd_display))
    if git_line:
        lines.append(kv("git", git_line))
    lines.append(kv("hub", f"{conn_dot} {ep}"))
    lines.append(kv("auth", auth_str))
    lines.append(kv("section", snapshot.section or "\u2013"))
    lines.append(kv("mode", snapshot.mode or "\u2013"))
    if workers:
        lines.append(kv("workers", wrk_str))

    if snapshot.goal_active:
        lines.append(kv("goal", _shorten_path(snapshot.goal_active, max_val)))

    return lines[:COMPACT_HEADER_LINES]


def format_status_compact_right(
    snapshot: StatusSnapshot,
    *,
    color: bool = True,
    right_width: int = 30,
) -> list[str]:
    lines = format_status_lines(snapshot, color=color, width=right_width)
    padded = []
    for line in lines:
        visible_len = _visible_length(line)
        if visible_len >= right_width:
            padded.append(line)
        else:
            pad = right_width - visible_len
            padded.append(" " * pad + line)
    return padded


def _visible_length(text: str) -> int:
    import re
    ansi_re = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
    return len(ansi_re.sub("", text))
