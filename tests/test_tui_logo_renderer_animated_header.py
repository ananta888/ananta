from __future__ import annotations

from client_surfaces.operator_tui.logo_renderer import animated_header


def test_animated_header_static_when_disabled(monkeypatch):
    monkeypatch.setenv("ANANTA_TUI_LOGO_ANIMATION", "off")
    monkeypatch.setattr(animated_header, "_CACHE", type("C", (), {"get_ansi_frames": lambda self, **k: [["static"]]} )())

    lines = animated_header.render_ansi_header_logo(cols=40, rows=8, color=True, t_now=1.0)
    assert lines == ["static"]


def test_animated_header_selects_frame_by_fps(monkeypatch):
    monkeypatch.setenv("ANANTA_TUI_LOGO_ANIMATION", "pulse")
    monkeypatch.setenv("ANANTA_TUI_LOGO_FPS", "4")
    monkeypatch.setattr(
        animated_header,
        "_CACHE",
        type("C", (), {"get_ansi_frames": lambda self, **k: [["f0"], ["f1"], ["f2"], ["f3"]]} )(),
    )

    # floor(0.51 * 4) = 2
    lines = animated_header.render_ansi_header_logo(cols=40, rows=8, color=True, t_now=0.51)
    assert lines == ["f2"]


def test_animated_header_accepts_rotate_hint_preset(monkeypatch):
    monkeypatch.setenv("ANANTA_TUI_LOGO_ANIMATION", "rotate_hint")
    monkeypatch.setenv("ANANTA_TUI_LOGO_FPS", "5")
    monkeypatch.setattr(
        animated_header,
        "_CACHE",
        type("C", (), {"get_ansi_frames": lambda self, **k: [["r0"], ["r1"], ["r2"]]} )(),
    )
    lines = animated_header.render_ansi_header_logo(cols=40, rows=8, color=True, t_now=0.41)
    assert lines in (["r1"], ["r2"])


def test_stream_frame_sequence_hides_and_restores_cursor():
    lines = animated_header.stream_frame_sequence(frame_sequence="PAYLOAD", rows=8, hide_cursor=True)
    assert len(lines) == 8
    assert lines[0].startswith("\x1b7\x1b[?25l")
    assert "\x1b[9;1H" in lines[0]
    assert "\x1b[?25h\x1b8" in lines[0]
