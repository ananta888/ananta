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
    repo_root, git_branch, git_dirty = _git_info(cwd)
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
        endpoint=endpoint,
        auth_state=auth_state,
        section=section,
    )


def _safe_cwd() -> str:
    try:
        return os.getcwd()
    except (OSError, PermissionError):
        return ""


def _git_info(cwd: str) -> tuple[str, str, bool]:
    if not cwd:
        return "", "", False
    try:
        import subprocess
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2, cwd=cwd,
        ).stdout.strip()
        if not root:
            return "", "", False
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2, cwd=cwd,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=2, cwd=cwd,
        ).stdout.strip()
        return root, branch, bool(status)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "", "", False


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
    uptime_str = _format_duration(snapshot.uptime_seconds)

    label_style = "\x1b[1m" if color else ""
    reset_style = "\x1b[0m" if color else ""
    dim_style = "\x1b[2m" if color else ""

    def kv(label: str, value: str) -> str:
        return f"{dim_style}{label}:{reset_style} {label_style}{value}{reset_style}"

    cwd_display = _repo_relative_path(snapshot.cwd, snapshot.repo_root)
    if not cwd_display:
        cwd_display = snapshot.cwd
    if not cwd_display:
        cwd_display = "?"

    git_info = ""
    if snapshot.git_branch:
        dirty_mark = " \u2717" if snapshot.git_dirty else ""
        git_info = f"{snapshot.git_branch}{dirty_mark}"

    endpoint_display = snapshot.endpoint or "?"
    if endpoint_display and endpoint_display != "?":
        endpoint_display = endpoint_display.replace("http://", "").replace("https://", "")

    if width < 50:
        lines = [
            kv("cwd", _shorten_path(cwd_display, 24)),
            kv("up", uptime_str),
        ]
        if git_info:
            lines.append(kv("git", git_info))
        lines.extend([
            kv("wrk", str(snapshot.workers_connected)),
            kv("q", str(snapshot.tasks_queued)),
            kv("run", str(snapshot.tasks_running)),
            kv("ok", str(snapshot.tasks_completed)),
            kv("ko", str(snapshot.tasks_failed)),
            kv("mode", snapshot.mode),
        ])
    else:
        lines = [
            kv("Cwd", _shorten_path(cwd_display, 28)),
        ]
        if git_info:
            lines.append(kv("Git", git_info))
        lines.extend([
            kv("Endpoint", endpoint_display),
            kv("Auth", snapshot.auth_state),
            kv("Section", snapshot.section),
            kv("Uptime", uptime_str),
            kv("Workers", str(snapshot.workers_connected)),
        ])

    if snapshot.goal_active:
        lines.append(kv("Goal", snapshot.goal_active[:28]))

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
