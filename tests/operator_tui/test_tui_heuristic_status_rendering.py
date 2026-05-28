"""Tests für TUI Heuristik-Status-Anzeige (T07.05)."""
import pytest
from client_surfaces.operator_tui.renderer import _overlay_snake_score_header


def _make_lines(width: int = 80, count: int = 3) -> list[str]:
    return [" " * width for _ in range(count)]


def _strip_ansi(text: str) -> str:
    import re
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)


def test_shadow_marker_shown_when_heuristic_mode_shadow():
    lines = _make_lines()
    game = {"score": 0, "speed_level": 3, "heuristic_mode": "shadow"}
    result = _overlay_snake_score_header(lines, game, width=80)
    rendered = " ".join(_strip_ansi(l) for l in result)
    assert "[DSL: shadow]" in rendered


def test_experimental_marker_shown_when_heuristic_mode_experimental():
    lines = _make_lines()
    game = {"score": 0, "speed_level": 3, "heuristic_mode": "experimental"}
    result = _overlay_snake_score_header(lines, game, width=80)
    rendered = " ".join(_strip_ansi(l) for l in result)
    assert "[DSL: exp]" in rendered


def test_active_marker_shown_when_heuristic_mode_active():
    lines = _make_lines()
    game = {"score": 0, "speed_level": 3, "heuristic_mode": "active"}
    result = _overlay_snake_score_header(lines, game, width=80)
    rendered = " ".join(_strip_ansi(l) for l in result)
    assert "[DSL: active]" in rendered


def test_no_marker_when_heuristic_mode_absent():
    """Marker NICHT angezeigt wenn heuristic_mode fehlt."""
    lines = _make_lines()
    game = {"score": 5, "speed_level": 3}
    result = _overlay_snake_score_header(lines, game, width=80)
    rendered = " ".join(_strip_ansi(l) for l in result)
    assert "[DSL:" not in rendered


def test_no_marker_when_heuristic_mode_is_none():
    """Marker NICHT angezeigt wenn heuristic_mode=None."""
    lines = _make_lines()
    game = {"score": 5, "speed_level": 3, "heuristic_mode": None}
    result = _overlay_snake_score_header(lines, game, width=80)
    rendered = " ".join(_strip_ansi(l) for l in result)
    assert "[DSL:" not in rendered


def test_score_still_shown_with_heuristic_mode():
    """Score-Anzeige bleibt erhalten wenn heuristic_mode gesetzt."""
    lines = _make_lines()
    game = {"score": 42, "speed_level": 2, "heuristic_mode": "shadow"}
    result = _overlay_snake_score_header(lines, game, width=120)
    rendered = " ".join(_strip_ansi(l) for l in result)
    assert "score: 42" in rendered
    assert "[DSL: shadow]" in rendered


def test_unknown_heuristic_mode_shows_generic_badge():
    """Unbekannter Modus: generisches Badge."""
    lines = _make_lines()
    game = {"score": 0, "speed_level": 3, "heuristic_mode": "custom_mode"}
    result = _overlay_snake_score_header(lines, game, width=120)
    rendered = " ".join(_strip_ansi(l) for l in result)
    assert "[DSL: custom_mode]" in rendered


def test_empty_lines_does_not_crash():
    """Leere Liste crasht nicht."""
    result = _overlay_snake_score_header([], {"heuristic_mode": "shadow"}, width=80)
    assert result == []
