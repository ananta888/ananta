from __future__ import annotations

import pytest

from client_surfaces.operator_tui.scroll.scroll_context import ScrollContext


def _ctx(content: int, viewport: int, offset: int = 0) -> ScrollContext:
    return ScrollContext(id="test", label="Test", content_height=content, viewport_height=viewport, offset=offset)


def test_max_scroll_positive():
    ctx = _ctx(100, 20)
    assert ctx.max_scroll == 80


def test_max_scroll_zero_when_fits():
    assert _ctx(10, 20).max_scroll == 0
    assert _ctx(20, 20).max_scroll == 0


def test_offset_clamped_on_init():
    ctx = _ctx(10, 20, offset=999)
    assert ctx.offset == 0


def test_scroll_line_down():
    ctx = _ctx(100, 20)
    moved = ctx.scroll_line_down(5)
    assert moved is True
    assert ctx.offset == 5


def test_scroll_line_up():
    ctx = _ctx(100, 20, offset=10)
    moved = ctx.scroll_line_up(3)
    assert moved is True
    assert ctx.offset == 7


def test_scroll_line_up_clamps_at_zero():
    ctx = _ctx(100, 20, offset=2)
    ctx.scroll_line_up(10)
    assert ctx.offset == 0


def test_scroll_line_down_clamps_at_max():
    ctx = _ctx(100, 20)
    ctx.scroll_line_down(999)
    assert ctx.offset == ctx.max_scroll


def test_scroll_page_up():
    ctx = _ctx(100, 10, offset=50)
    moved = ctx.scroll_page_up()
    assert moved is True
    assert ctx.offset == 50 - (10 - ctx.page_overlap)


def test_scroll_page_down():
    ctx = _ctx(100, 10)
    ctx.scroll_page_down()
    assert ctx.offset == 10 - ctx.page_overlap


def test_scroll_home():
    ctx = _ctx(100, 20, offset=50)
    moved = ctx.scroll_home()
    assert moved is True
    assert ctx.offset == 0


def test_scroll_end():
    ctx = _ctx(100, 20)
    moved = ctx.scroll_end()
    assert moved is True
    assert ctx.offset == ctx.max_scroll


def test_no_move_at_top_line_up():
    ctx = _ctx(100, 20, offset=0)
    moved = ctx.scroll_line_up()
    assert moved is False


def test_no_move_at_bottom_line_down():
    ctx = _ctx(100, 20, offset=80)
    moved = ctx.scroll_line_down()
    assert moved is False


def test_empty_content():
    ctx = _ctx(0, 20)
    assert ctx.max_scroll == 0
    ctx.scroll_line_down()
    assert ctx.offset == 0


def test_exact_fit_content():
    ctx = _ctx(20, 20)
    assert ctx.max_scroll == 0
    ctx.scroll_page_down()
    assert ctx.offset == 0


def test_large_content():
    ctx = _ctx(10000, 20)
    ctx.scroll_end()
    assert ctx.offset == 9980


def test_is_at_bottom_when_at_end():
    ctx = _ctx(100, 20)
    ctx.scroll_end()
    assert ctx.is_at_bottom()


def test_is_at_bottom_false_when_scrolled_up():
    ctx = _ctx(100, 20, offset=0)
    assert not ctx.is_at_bottom()


def test_is_scrollable():
    assert _ctx(100, 20).is_scrollable()
    assert not _ctx(10, 20).is_scrollable()


def test_update_dimensions_clamps():
    ctx = _ctx(100, 20, offset=80)
    ctx.update_dimensions(content_height=25, viewport_height=20)
    assert ctx.offset == 5


def test_diagnostics_keys():
    ctx = _ctx(100, 20)
    d = ctx.diagnostics()
    assert "id" in d and "offset" in d and "max_scroll" in d


def test_page_overlap_is_at_least_one():
    ctx = _ctx(100, 10)
    ctx.scroll_page_down()
    assert ctx.offset < ctx.viewport_height
