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
