from __future__ import annotations

import re

from client_surfaces.operator_tui.logo_renderer import animated_header
from client_surfaces.operator_tui.logo_renderer.frame import PixelFrame
from client_surfaces.operator_tui.logo_renderer.kitty import KittyRenderer
from client_surfaces.operator_tui.logo_renderer.sixel import SixelRenderer
from client_surfaces.operator_tui.models import FocusPane, OperatorState
from client_surfaces.operator_tui.renderer import render_operator_shell
from agent.cli.main import _run_tui

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
    from client_surfaces.operator_tui.logo_renderer.frame import PixelFrame

    monkeypatch.setattr(
        "client_surfaces.operator_tui.logo_renderer.kitty.frame_from_svg",
        lambda **kwargs: PixelFrame(width_px=10, height_px=10, rgba=b"\x00" * 400),
    )
    renderer = KittyRenderer(image_id=42, placement_id=7)
    one = renderer.render_frame(width_cells=50, height_cells=8, t=0.0)
    two = renderer.render_frame(width_cells=50, height_cells=8, t=0.1)
    assert "i=42" in one.sequence and "p=7" in one.sequence
    assert "i=42" in two.sequence and "p=7" in two.sequence


def test_kitty_sequence_deletes_previous_image(monkeypatch):
    from client_surfaces.operator_tui.logo_renderer.frame import PixelFrame

    monkeypatch.setattr(
        "client_surfaces.operator_tui.logo_renderer.kitty.frame_from_svg",
        lambda **kwargs: PixelFrame(width_px=10, height_px=10, rgba=b"\x00" * 400),
    )
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
    from client_surfaces.operator_tui.logo_renderer.frame import PixelFrame

    monkeypatch.setattr(
        "client_surfaces.operator_tui.logo_renderer.sixel.frame_from_svg",
        lambda **kwargs: PixelFrame(width_px=10, height_px=10, rgba=b"\x00" * 400),
    )
    renderer = SixelRenderer()
    renderer._tool = "/usr/bin/img2sixel"
    monkeypatch.setattr(renderer, "_encode_sixel_from_frame", lambda _image: "\x1bPqFAKE\x1b\\")
    frame = renderer.render_frame(width_cells=50, height_cells=8, t=0.0)
    assert frame.sequence.startswith("\x1b7\x1b[1;1H")
    assert "\x1b[9;1H" in frame.sequence
    assert frame.sequence.endswith("\x1b8")


def test_graphics_backends_expose_common_control_methods(monkeypatch):
    from client_surfaces.operator_tui.logo_renderer.frame import PixelFrame

    monkeypatch.setattr(
        "client_surfaces.operator_tui.logo_renderer.kitty.frame_from_svg",
        lambda **kwargs: PixelFrame(width_px=10, height_px=10, rgba=b"\x00" * 400),
    )
    monkeypatch.setattr(
        "client_surfaces.operator_tui.logo_renderer.sixel.frame_from_svg",
        lambda **kwargs: PixelFrame(width_px=10, height_px=10, rgba=b"\x00" * 400),
    )

    kitty = KittyRenderer(image_id=5, placement_id=7)
    sixel = SixelRenderer()
    sixel._tool = "/usr/bin/img2sixel"
    monkeypatch.setattr(sixel, "_encode_sixel_from_frame", lambda _image: "\x1bPqFAKE\x1b\\")

    assert kitty.supports_animation() is True
    assert kitty.supports_truecolor() is True
    assert kitty.get_capabilities()["renderer"] == "kitty"
    assert "\x1b_Ga=d" in kitty.clear_region(x=1, y=1, width=10, height=5)

    assert sixel.supports_animation() is True
    assert sixel.supports_truecolor() is True
    assert sixel.get_capabilities()["renderer"] == "sixel"
    assert "\x1b[0J" in sixel.clear_region(x=1, y=1, width=10, height=5)


def test_header_small_width_keeps_stable_fallback():
    state = OperatorState(endpoint="http://localhost:5000", auth_state="token", focus=FocusPane.NAVIGATION)
    rendered = render_operator_shell(state, width=72, height=24, splash=None)
    assert len(rendered.splitlines()) >= 10


def test_render_once_works_without_interactive_tty(capsys, monkeypatch):
    monkeypatch.setenv("ANANTA_TUI_LOGO_RENDERER", "ansi")
    rc = _run_tui(["--render-once", "--skip-splash", "--width", "90", "--height", "20"])
    out = capsys.readouterr().out
    assert rc == 0
    assert len(out) > 0


def test_animation_disabled_uses_single_static_frame(monkeypatch):
    monkeypatch.setenv("ANANTA_TUI_LOGO_ANIMATION", "static")
    calls: list[dict[str, object]] = []

    class _FakeCache:
        def get_ansi_frames(self, **kwargs):
            calls.append(kwargs)
            return [["static"]]

    monkeypatch.setattr(animated_header, "_CACHE", _FakeCache())
    lines = animated_header.render_ansi_header_logo(cols=40, rows=8, color=True, t_now=123.0)
    assert lines == ["static"]
    assert len(calls) == 1
    assert calls[0]["frame_count"] == 1


def test_render_header_logo_3d_stream_mode_uses_offscreen_frame(monkeypatch):
    monkeypatch.setenv("ANANTA_TUI_ENABLE_3D", "1")
    monkeypatch.setenv("ANANTA_TUI_LOGO_STREAM_INLINE", "1")
    monkeypatch.setenv("ANANTA_TUI_LOGO_RENDERER", "kitty")
    monkeypatch.setenv("KITTY_WINDOW_ID", "11")
    monkeypatch.setattr(
        "client_surfaces.operator_tui.logo_renderer.animated_header._pick_3d_renderer",
        lambda pref: type(
            "R",
            (),
            {
                "name": "test3d",
                "render_scene": staticmethod(
                    lambda **kwargs: PixelFrame(width_px=12, height_px=12, rgba=b"\x00" * (12 * 12 * 4), metadata={"renderer": "test3d"})
                ),
            },
        )(),
    )
    monkeypatch.setattr(
        "client_surfaces.operator_tui.logo_renderer.kitty.KittyRenderer.render_pixel_sequence",
        lambda self, *, frame, height_cells: "\x1b_Ga=T,f=100,m=0;ZW5jb2RlZA==\x1b\\",
    )
    lines = animated_header.render_header_logo(cols=40, rows=8, color=True, t_now=1.0)
    assert lines is not None
    assert lines[0].startswith("\x1b7")
