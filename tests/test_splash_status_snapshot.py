from __future__ import annotations

from agent.cli.status_snapshot import (
    StatusSnapshot,
    format_status_lines,
    format_status_compact_right,
    _format_duration,
    _visible_length,
)


def test_format_duration_seconds():
    assert _format_duration(0) == "0s"
    assert _format_duration(30) == "30s"
    assert _format_duration(120) == "2m00s"
    assert _format_duration(3661) == "1h01m01s"
    assert _format_duration(3600) == "1h00m00s"


def test_format_duration_negative():
    assert _format_duration(-5) == "0s"


def test_format_status_lines_count():
    snap = StatusSnapshot(
        tasks_queued=3,
        tasks_running=2,
        tasks_completed=10,
        tasks_failed=1,
        workers_connected=4,
        uptime_seconds=3600,
        mode="dashboard",
    )
    lines = format_status_lines(snap, color=False)
    assert len(lines) == 8
    assert any("Uptime" in l for l in lines)
    assert any("Workers" in l for l in lines)
    assert any("Queued" in l for l in lines)
    assert any("Running" in l for l in lines)
    assert any("Completed" in l for l in lines)
    assert any("Failed" in l for l in lines)
    assert any("Mode" in l for l in lines)


def test_format_status_lines_narrow():
    snap = StatusSnapshot(
        tasks_queued=3,
        tasks_running=2,
        tasks_completed=10,
        tasks_failed=1,
        workers_connected=4,
        uptime_seconds=3600,
        mode="dashboard",
    )
    lines = format_status_lines(snap, color=False, width=30)
    assert any("up" in l for l in lines)
    assert any("wrk" in l for l in lines)


def test_format_status_lines_with_goal():
    snap = StatusSnapshot(
        tasks_queued=0,
        tasks_running=0,
        tasks_completed=0,
        tasks_failed=0,
        workers_connected=1,
        uptime_seconds=100,
        mode="dashboard",
        goal_active="Build the thing",
    )
    lines = format_status_lines(snap, color=False)
    assert any("Goal" in l for l in lines)


def test_format_status_compact_right_padding():
    snap = StatusSnapshot(tasks_queued=5, uptime_seconds=60, workers_connected=2)
    lines = format_status_compact_right(snap, color=False, right_width=30)
    for line in lines:
        visible = _visible_length(line)
        assert visible <= 30, f"Line visible length {visible} exceeds {right_width}: {line!r}"


def test_status_snapshot_defaults():
    snap = StatusSnapshot()
    assert snap.tasks_queued == 0
    assert snap.tasks_running == 0
    assert snap.tasks_completed == 0
    assert snap.tasks_failed == 0
    assert snap.workers_connected == 0
    assert snap.uptime_seconds == 0.0
    assert snap.mode == "dashboard"


def test_visible_length_ansi():
    text = "\x1b[1mhello\x1b[0m"
    assert _visible_length(text) == 5
