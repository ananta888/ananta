from __future__ import annotations

from client_surfaces.operator_tui.scroll.scrollbar_renderer import (
    minimal_scroll_indicator,
    render_scrollbar_column,
    scrollbar_thumb_info,
)


def test_no_scrollbar_when_fits():
    col = render_scrollbar_column(content_height=10, viewport_height=20, offset=0, height=10)
    assert all(c == " " for c in col)


def test_scrollbar_length_matches_height():
    for h in [3, 5, 10, 20]:
        col = render_scrollbar_column(content_height=100, viewport_height=20, offset=0, height=h)
        assert len(col) == h


def test_scrollbar_top_state():
    col = render_scrollbar_column(content_height=100, viewport_height=20, offset=0, height=10)
    assert "▲" in col or "^" in col
    assert "█" in col or "|" in col


def test_scrollbar_bottom_state():
    col = render_scrollbar_column(content_height=100, viewport_height=20, offset=80, height=10)
    assert "▼" in col or "v" in col
    assert "█" in col or "|" in col


def test_scrollbar_middle_state():
    col = render_scrollbar_column(content_height=100, viewport_height=20, offset=40, height=10)
    thumb_indices = [i for i, c in enumerate(col) if c in ("█", "|")]
    assert 0 < min(thumb_indices) < len(col) - 1


def test_ascii_fallback():
    col = render_scrollbar_column(content_height=100, viewport_height=20, offset=0, height=5, ascii_fallback=True)
    assert "^" in col or "v" in col or "|" in col


def test_thumb_position_top():
    info = scrollbar_thumb_info(content_height=100, viewport_height=20, offset=0)
    assert info["thumb_pos"] == 0


def test_thumb_position_bottom():
    info = scrollbar_thumb_info(content_height=100, viewport_height=20, offset=80)
    assert info["thumb_pos"] == info["thumb_h"] or info["thumb_pos"] > 0


def test_thumb_pos_increases_with_offset():
    info1 = scrollbar_thumb_info(content_height=100, viewport_height=20, offset=0)
    info2 = scrollbar_thumb_info(content_height=100, viewport_height=20, offset=40)
    info3 = scrollbar_thumb_info(content_height=100, viewport_height=20, offset=80)
    assert info1["thumb_pos"] <= info2["thumb_pos"] <= info3["thumb_pos"]


def test_minimal_indicator_at_top():
    s = minimal_scroll_indicator(offset=0, max_scroll=50)
    assert "▼" in s
    assert "▲" not in s


def test_minimal_indicator_at_bottom():
    s = minimal_scroll_indicator(offset=50, max_scroll=50)
    assert "▲" in s
    assert "▼" not in s


def test_minimal_indicator_middle():
    s = minimal_scroll_indicator(offset=25, max_scroll=50)
    assert "▲" in s and "▼" in s


def test_minimal_indicator_no_scroll():
    s = minimal_scroll_indicator(offset=0, max_scroll=0)
    assert s == ""


def test_scrollbar_snapshot_stable():
    col1 = render_scrollbar_column(content_height=100, viewport_height=10, offset=30, height=8)
    col2 = render_scrollbar_column(content_height=100, viewport_height=10, offset=30, height=8)
    assert col1 == col2
