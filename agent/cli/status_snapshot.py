from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable


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
    timestamp: float = field(default_factory=time.time)


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

    if width < 40:
        lines = [
            kv("up", uptime_str),
            kv("wrk", str(snapshot.workers_connected)),
            kv("q", str(snapshot.tasks_queued)),
            kv("run", str(snapshot.tasks_running)),
            kv("ok", str(snapshot.tasks_completed)),
            kv("ko", str(snapshot.tasks_failed)),
            kv("mode", snapshot.mode),
        ]
    else:
        lines = [
            kv("Uptime", uptime_str),
            kv("Workers", str(snapshot.workers_connected)),
            kv("Queued", str(snapshot.tasks_queued)),
            kv("Running", str(snapshot.tasks_running)),
            kv("Completed", str(snapshot.tasks_completed)),
            kv("Failed", str(snapshot.tasks_failed)),
            kv("Mode", snapshot.mode),
        ]

    goal_line = ""
    if snapshot.goal_active:
        goal_line = kv("Goal", snapshot.goal_active[:28])
    lines.append(goal_line)

    return lines


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
