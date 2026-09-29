from __future__ import annotations

from agent.cli.logo_layout import (
    COMPACT_HEADER_LINES,
    render_compact_header,
    _visible_length,
    _max_line_width,
)
from agent.cli.status_snapshot import StatusSnapshot


def test_compact_header_is_8_lines():
    snap = StatusSnapshot(tasks_queued=3, workers_connected=2, uptime_seconds=120)
    lines = render_compact_header(snap, terminal_width=120, color=False)
    assert len(lines) == COMPACT_HEADER_LINES


def test_compact_header_all_strings():
    snap = StatusSnapshot(tasks_queued=3, workers_connected=2, uptime_seconds=120)
    lines = render_compact_header(snap, terminal_width=120, color=False)
    for line in lines:
        assert isinstance(line, str)


def test_compact_header_narrow_fallback():
    snap = StatusSnapshot(tasks_queued=3, uptime_seconds=60)
    lines = render_compact_header(snap, terminal_width=40, color=False)
    assert len(lines) == COMPACT_HEADER_LINES


def test_compact_header_no_snapshot():
    lines = render_compact_header(None, terminal_width=120, color=False)
    assert len(lines) == COMPACT_HEADER_LINES


def test_compact_header_no_snapshot_narrow():
    lines = render_compact_header(None, terminal_width=40, color=False)
    assert len(lines) == COMPACT_HEADER_LINES


def test_compact_header_with_color():
    snap = StatusSnapshot(workers_connected=3, uptime_seconds=300)
    lines = render_compact_header(snap, terminal_width=120, color=True)
    assert len(lines) == COMPACT_HEADER_LINES


def test_visible_length_empty():
    assert _visible_length("") == 0


def test_visible_length_plain():
    assert _visible_length("hello") == 5


def test_max_line_width_empty():
    assert _max_line_width([]) == 0


def test_max_line_width_various():
    assert _max_line_width(["a", "bb", "ccc"]) == 3
