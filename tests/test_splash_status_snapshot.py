from __future__ import annotations

from agent.cli.status_snapshot import (
    StatusSnapshot,
    collect_status,
    format_status_lines,
    format_status_compact_right,
    _format_duration,
    _visible_length,
    _shorten_path,
    _repo_relative_path,
    COMPACT_HEADER_LINES,
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
        cwd="/home/user/project",
        endpoint="http://localhost:5000",
        auth_state="token",
        section="goals",
    )
    lines = format_status_lines(snap, color=False)
    assert len(lines) <= COMPACT_HEADER_LINES
    assert any("Cwd" in l for l in lines)
    assert any("Endpoint" in l for l in lines)
    assert any("Auth" in l for l in lines)
    assert any("Section" in l for l in lines)
    assert any("Uptime" in l for l in lines)
    assert any("Workers" in l for l in lines)


def test_format_status_lines_with_git():
    snap = StatusSnapshot(
        git_branch="main",
        git_dirty=True,
        cwd="/repo/src",
        repo_root="/repo",
    )
    lines = format_status_lines(snap, color=False)
    assert any("Git" in l for l in lines)
    assert any("main" in l for l in lines)


def test_format_status_lines_narrow():
    snap = StatusSnapshot(
        tasks_queued=3,
        uptime_seconds=3600,
        mode="dashboard",
        cwd="/home/user/project",
    )
    lines = format_status_lines(snap, color=False, width=40)
    assert any("cwd" in l for l in lines)
    assert any("up" in l for l in lines)


def test_format_status_lines_with_goal():
    snap = StatusSnapshot(
        goal_active="Build the thing",
        cwd="/home/user",
    )
    lines = format_status_lines(snap, color=False)
    assert any("Goal" in l for l in lines)


def test_format_status_compact_right_padding():
    snap = StatusSnapshot(tasks_queued=5, uptime_seconds=60, workers_connected=2, cwd="/tmp")
    lines = format_status_compact_right(snap, color=False, right_width=30)
    for line in lines:
        visible = _visible_length(line)
        assert visible <= 30, f"Line visible length {visible} exceeds 30: {line!r}"


def test_status_snapshot_defaults():
    snap = StatusSnapshot()
    assert snap.tasks_queued == 0
    assert snap.tasks_running == 0
    assert snap.tasks_completed == 0
    assert snap.tasks_failed == 0
    assert snap.workers_connected == 0
    assert snap.uptime_seconds == 0.0
    assert snap.mode == "dashboard"
    assert snap.cwd == ""
    assert snap.repo_root == ""
    assert snap.git_branch == ""
    assert snap.git_dirty is False
    assert snap.endpoint == ""
    assert snap.auth_state == ""
    assert snap.section == ""


def test_visible_length_ansi():
    text = "\x1b[1mhello\x1b[0m"
    assert _visible_length(text) == 5


def test_shorten_path_short():
    assert _shorten_path("hello", 10) == "hello"


def test_shorten_path_long():
    result = _shorten_path("a" * 50, 20)
    assert len(result) <= 20
    assert "..." in result


def test_repo_relative_path_no_repo():
    assert _repo_relative_path("/a/b", "") == "/a/b"


def test_repo_relative_path_inside():
    assert _repo_relative_path("/repo/src/lib", "/repo") == "src/lib"


def test_repo_relative_path_deep():
    result = _repo_relative_path("/repo/a/b/c/d", "/repo")
    assert "..." in result


def test_collect_status_basic():
    snap = collect_status(mode="test", endpoint="http://x:5000", section="goals")
    assert snap.mode == "test"
    assert snap.endpoint == "http://x:5000"
    assert snap.section == "goals"
    assert isinstance(snap.cwd, str)
