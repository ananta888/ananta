from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def isolated_config_dir(tmp_path, monkeypatch):
    """Redirect _config_dir() to a tmp directory for each test."""
    import client_surfaces.operator_tui.snake_persistence as sp

    monkeypatch.setattr(sp, "_config_dir", lambda: tmp_path / "ananta")
    return tmp_path / "ananta"


# ── highscore ─────────────────────────────────────────────────────────────────


def test_load_snake_scores_defaults_when_missing(isolated_config_dir):
    from client_surfaces.operator_tui.snake_persistence import load_snake_scores

    scores = load_snake_scores()
    assert scores == {"high": 0, "last": 0, "games": 0}


def test_save_snake_score_creates_file(isolated_config_dir):
    from client_surfaces.operator_tui.snake_persistence import save_snake_score

    result = save_snake_score(42)
    assert result["high"] == 42
    assert result["last"] == 42
    assert result["games"] == 1
    assert (isolated_config_dir / "snake_scores.json").exists()


def test_save_snake_score_updates_highscore(isolated_config_dir):
    from client_surfaces.operator_tui.snake_persistence import save_snake_score

    save_snake_score(10)
    save_snake_score(50)
    result = save_snake_score(30)
    assert result["high"] == 50
    assert result["last"] == 30
    assert result["games"] == 3


def test_save_snake_score_does_not_lower_highscore(isolated_config_dir):
    from client_surfaces.operator_tui.snake_persistence import save_snake_score

    save_snake_score(100)
    result = save_snake_score(5)
    assert result["high"] == 100


def test_load_snake_scores_handles_corrupt_file(isolated_config_dir):
    from client_surfaces.operator_tui.snake_persistence import load_snake_scores

    path = isolated_config_dir / "snake_scores.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("NOT JSON", encoding="utf-8")
    scores = load_snake_scores()
    assert scores == {"high": 0, "last": 0, "games": 0}


# ── tutor config ──────────────────────────────────────────────────────────────


def test_load_tutor_config_defaults(isolated_config_dir):
    from client_surfaces.operator_tui.snake_persistence import load_tutor_config

    cfg = load_tutor_config()
    assert cfg["mode"] == "overview"
    assert cfg["silent"] is False
    assert cfg["visited_sections"] == []
    assert cfg["tutorial_progress"] == {}


def test_set_tutor_mode_persists(isolated_config_dir):
    from client_surfaces.operator_tui.snake_persistence import get_tutor_mode, set_tutor_mode

    set_tutor_mode("expert")
    assert get_tutor_mode() == "expert"


def test_set_tutor_mode_rejects_invalid(isolated_config_dir):
    from client_surfaces.operator_tui.snake_persistence import get_tutor_mode, set_tutor_mode

    set_tutor_mode("invalid")
    assert get_tutor_mode() == "overview"


def test_tutor_silent_toggle(isolated_config_dir):
    from client_surfaces.operator_tui.snake_persistence import is_tutor_silent, set_tutor_silent

    assert not is_tutor_silent()
    set_tutor_silent(True)
    assert is_tutor_silent()
    set_tutor_silent(False)
    assert not is_tutor_silent()


# ── section visits ────────────────────────────────────────────────────────────


def test_mark_section_visited_first_time_returns_true(isolated_config_dir):
    from client_surfaces.operator_tui.snake_persistence import mark_section_visited

    assert mark_section_visited("dashboard") is True


def test_mark_section_visited_second_time_returns_false(isolated_config_dir):
    from client_surfaces.operator_tui.snake_persistence import mark_section_visited

    mark_section_visited("goals")
    assert mark_section_visited("goals") is False


def test_get_visited_sections_accumulates(isolated_config_dir):
    from client_surfaces.operator_tui.snake_persistence import get_visited_sections, mark_section_visited

    mark_section_visited("tasks")
    mark_section_visited("artifacts")
    mark_section_visited("tasks")  # duplicate
    assert sorted(get_visited_sections()) == ["artifacts", "tasks"]


# ── tutorial progress ─────────────────────────────────────────────────────────


def test_get_tutorial_progress_returns_minus_one_when_not_started(isolated_config_dir):
    from client_surfaces.operator_tui.snake_persistence import get_tutorial_progress

    assert get_tutorial_progress("intro") == -1


def test_save_and_get_tutorial_progress(isolated_config_dir):
    from client_surfaces.operator_tui.snake_persistence import get_tutorial_progress, save_tutorial_progress

    save_tutorial_progress("intro", 3)
    assert get_tutorial_progress("intro") == 3


def test_reset_tutorial_progress(isolated_config_dir):
    from client_surfaces.operator_tui.snake_persistence import (
        get_tutorial_progress,
        reset_tutorial_progress,
        save_tutorial_progress,
    )

    save_tutorial_progress("snake_mode", 5)
    reset_tutorial_progress("snake_mode")
    assert get_tutorial_progress("snake_mode") == -1


def test_reset_tutorial_progress_unknown_name_is_noop(isolated_config_dir):
    from client_surfaces.operator_tui.snake_persistence import reset_tutorial_progress

    reset_tutorial_progress("nonexistent")  # must not raise


def test_save_and_load_tui_chat_settings_scoped_by_cwd(isolated_config_dir):
    from client_surfaces.operator_tui.snake_persistence import load_tui_chat_settings, save_tui_chat_settings

    save_tui_chat_settings({"chat_backend": "lmstudio", "chat_max_tokens": 1200}, cwd="/tmp/project-a")
    save_tui_chat_settings({"chat_backend": "ananta-worker", "chat_max_tokens": 800}, cwd="/tmp/project-b")

    cfg_a = load_tui_chat_settings(cwd="/tmp/project-a")
    cfg_b = load_tui_chat_settings(cwd="/tmp/project-b")

    assert cfg_a.get("chat_backend") == "lmstudio"
    assert cfg_a.get("chat_max_tokens") == 1200
    assert cfg_b.get("chat_backend") == "ananta-worker"
    assert cfg_b.get("chat_max_tokens") == 800


def test_save_tui_chat_settings_ignores_non_scalar_values(isolated_config_dir):
    from client_surfaces.operator_tui.snake_persistence import load_tui_chat_settings, save_tui_chat_settings

    save_tui_chat_settings(
        {
            "chat_backend": "lmstudio",
            "chat_context_chars": 3000,
            "nested": {"invalid": True},
            "list": [1, 2, 3],
        },
        cwd="/tmp/project-c",
    )
    cfg = load_tui_chat_settings(cwd="/tmp/project-c")
    assert cfg == {"chat_backend": "lmstudio", "chat_context_chars": 3000}
