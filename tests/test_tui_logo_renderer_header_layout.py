from __future__ import annotations

import re

from client_surfaces.operator_tui.logo_renderer import animated_header
from client_surfaces.operator_tui.logo_renderer.kitty import KittyRenderer
from client_surfaces.operator_tui.logo_renderer.sixel import SixelRenderer
from client_surfaces.operator_tui.models import FocusPane, OperatorState
from client_surfaces.operator_tui.renderer import render_operator_shell

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def test_render_header_logo_none_returns_none(monkeypatch):
    monkeypatch.setenv("ANANTA_TUI_LOGO", "0")
    lines = animated_header.render_header_logo(cols=40, rows=8, color=True, t_now=1.0)
    assert lines is None


def test_render_header_logo_forced_unavailable_renderer_falls_back_to_ansi(monkeypatch):
    monkeypatch.setenv("ANANTA_TUI_LOGO_RENDERER", "kitty")
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    lines = animated_header.render_header_logo(cols=40, rows=8, color=True, t_now=1.0)
    assert lines is not None
    assert len(lines) >= 1


def test_kitty_sequence_reuses_same_placement_zone(monkeypatch):
    monkeypatch.setattr("client_surfaces.operator_tui.logo_renderer.kitty.rasterize_svg_rgba", lambda **kwargs: object())
    monkeypatch.setattr("client_surfaces.operator_tui.logo_renderer.kitty.encode_png_bytes", lambda _img: b"abc")
    renderer = KittyRenderer(image_id=42, placement_id=7)
    one = renderer.render_frame(width_cells=50, height_cells=8, t=0.0)
    two = renderer.render_frame(width_cells=50, height_cells=8, t=0.1)
    assert "i=42" in one.sequence and "p=7" in one.sequence
    assert "i=42" in two.sequence and "p=7" in two.sequence


def test_kitty_sequence_deletes_previous_image(monkeypatch):
    monkeypatch.setattr("client_surfaces.operator_tui.logo_renderer.kitty.rasterize_svg_rgba", lambda **kwargs: object())
    monkeypatch.setattr("client_surfaces.operator_tui.logo_renderer.kitty.encode_png_bytes", lambda _img: b"abc")
    renderer = KittyRenderer(image_id=101, placement_id=202)
    frame = renderer.render_frame(width_cells=50, height_cells=8, t=0.0)
    assert "a=d" in frame.sequence
    assert "i=101" in frame.sequence


def test_shell_layout_keeps_header_and_status_separated(monkeypatch):
    monkeypatch.setenv("ANANTA_TUI_LOGO_RENDERER", "ansi")
    state = OperatorState(endpoint="http://localhost:5000", auth_state="token", focus=FocusPane.NAVIGATION)
    rendered = render_operator_shell(state, width=120, height=30, splash=None)
    first_line = rendered.splitlines()[0]
    plain = _ANSI_RE.sub("", first_line)
    assert "│" in plain


def test_sixel_frame_sequence_contains_position_and_restore(monkeypatch):
    monkeypatch.setattr("client_surfaces.operator_tui.logo_renderer.sixel.rasterize_svg_rgba", lambda **kwargs: object())
    renderer = SixelRenderer()
    renderer._tool = "/usr/bin/img2sixel"
    monkeypatch.setattr(renderer, "_encode_sixel_from_image", lambda _image: "\x1bPqFAKE\x1b\\")
    frame = renderer.render_frame(width_cells=50, height_cells=8, t=0.0)
    assert frame.sequence.startswith("\x1b7\x1b[1;1H")
    assert "\x1b[9;1H" in frame.sequence
    assert frame.sequence.endswith("\x1b8")
