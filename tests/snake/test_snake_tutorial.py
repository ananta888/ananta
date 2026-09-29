from __future__ import annotations

import time
from pathlib import Path

import pytest


@pytest.fixture()
def tutorials_dir(tmp_path, monkeypatch):
    """Redirect _TUTORIALS_DIR to a tmp directory."""
    import client_surfaces.operator_tui.snake_tutorial as st

    monkeypatch.setattr(st, "_TUTORIALS_DIR", tmp_path)
    return tmp_path


def _write_tutorial(tutorials_dir: Path, name: str, content: str) -> None:
    (tutorials_dir / f"{name}.yaml").write_text(content, encoding="utf-8")


_SIMPLE_YAML = """\
title: "Test Tutorial"
description: "A simple test tutorial."
steps:
  - id: "step_one"
    title: "Step One"
    task: "Do something."
    hint: "A hint."
    completion_event: "any_key"
  - id: "step_two"
    title: "Step Two"
    task: "Do more."
    hint: "Another hint."
    completion_event: "navigation_moved"
"""


# ── load_tutorial ─────────────────────────────────────────────────────────────


def test_load_tutorial_returns_none_for_missing_file(tutorials_dir):
    from client_surfaces.operator_tui.snake_tutorial import load_tutorial

    assert load_tutorial("nonexistent") is None


def test_load_tutorial_parses_valid_yaml(tutorials_dir):
    _write_tutorial(tutorials_dir, "test", _SIMPLE_YAML)
    from client_surfaces.operator_tui.snake_tutorial import load_tutorial

    t = load_tutorial("test")
    assert t is not None
    assert t["title"] == "Test Tutorial"
    assert t["step_count"] == 2
    assert t["steps"][0]["id"] == "step_one"
    assert t["steps"][1]["completion_event"] == "navigation_moved"


def test_load_tutorial_rejects_missing_steps(tutorials_dir):
    _write_tutorial(tutorials_dir, "bad", 'title: "No Steps"\n')
    from client_surfaces.operator_tui.snake_tutorial import load_tutorial

    assert load_tutorial("bad") is None


def test_load_tutorial_skips_steps_without_id(tutorials_dir):
    yaml = """\
title: "Partial"
steps:
  - id: "ok"
    title: "OK step"
    completion_event: "any_key"
  - title: "No ID step"
    completion_event: "any_key"
"""
    _write_tutorial(tutorials_dir, "partial", yaml)
    from client_surfaces.operator_tui.snake_tutorial import load_tutorial

    t = load_tutorial("partial")
    assert t is not None
    assert t["step_count"] == 1


# ── list_tutorials ────────────────────────────────────────────────────────────


def test_list_tutorials_empty_dir(tutorials_dir):
    from client_surfaces.operator_tui.snake_tutorial import list_tutorials

    assert list_tutorials() == []


def test_list_tutorials_returns_metadata(tutorials_dir):
    _write_tutorial(tutorials_dir, "aaa", _SIMPLE_YAML)
    from client_surfaces.operator_tui.snake_tutorial import list_tutorials

    items = list_tutorials()
    assert len(items) == 1
    assert items[0]["name"] == "aaa"
    assert items[0]["step_count"] == 2


# ── make_tutorial_state ────────────────────────────────────────────────────────


def test_make_tutorial_state_returns_none_for_unknown(tutorials_dir):
    from client_surfaces.operator_tui.snake_tutorial import make_tutorial_state

    assert make_tutorial_state("nope") is None


def test_make_tutorial_state_initial_step_zero(tutorials_dir):
    _write_tutorial(tutorials_dir, "t", _SIMPLE_YAML)
    from client_surfaces.operator_tui.snake_tutorial import make_tutorial_state

    s = make_tutorial_state("t")
    assert s is not None
    assert s["current_step"] == 0
    assert s["active"] is True
    assert s["steps_skipped"] == 0


def test_make_tutorial_state_respects_start_step(tutorials_dir):
    _write_tutorial(tutorials_dir, "t", _SIMPLE_YAML)
    from client_surfaces.operator_tui.snake_tutorial import make_tutorial_state

    s = make_tutorial_state("t", start_step=1)
    assert s["current_step"] == 1


def test_make_tutorial_state_clamps_start_step(tutorials_dir):
    _write_tutorial(tutorials_dir, "t", _SIMPLE_YAML)
    from client_surfaces.operator_tui.snake_tutorial import make_tutorial_state

    s = make_tutorial_state("t", start_step=99)
    assert s["current_step"] == 1  # clamped to step_count - 1


# ── get_current_step ──────────────────────────────────────────────────────────


def test_get_current_step_returns_step_data(tutorials_dir):
    _write_tutorial(tutorials_dir, "t", _SIMPLE_YAML)
    from client_surfaces.operator_tui.snake_tutorial import get_current_step, make_tutorial_state

    s = make_tutorial_state("t")
    step = get_current_step(s)
    assert step is not None
    assert step["id"] == "step_one"


def test_get_current_step_returns_none_when_inactive(tutorials_dir):
    from client_surfaces.operator_tui.snake_tutorial import get_current_step

    assert get_current_step({"active": False}) is None


# ── advance_step ──────────────────────────────────────────────────────────────


def test_advance_step_increments_index(tutorials_dir):
    _write_tutorial(tutorials_dir, "t", _SIMPLE_YAML)
    from client_surfaces.operator_tui.snake_tutorial import advance_step, make_tutorial_state

    s = make_tutorial_state("t")
    s2 = advance_step(s)
    assert s2["current_step"] == 1
    assert s2["active"] is True


def test_advance_step_marks_inactive_at_end(tutorials_dir):
    _write_tutorial(tutorials_dir, "t", _SIMPLE_YAML)
    from client_surfaces.operator_tui.snake_tutorial import advance_step, make_tutorial_state

    s = make_tutorial_state("t", start_step=1)
    s2 = advance_step(s)
    assert s2["active"] is False
    assert "completed_at" in s2


def test_advance_step_skipped_increments_counter(tutorials_dir):
    _write_tutorial(tutorials_dir, "t", _SIMPLE_YAML)
    from client_surfaces.operator_tui.snake_tutorial import advance_step, make_tutorial_state

    s = make_tutorial_state("t")
    s2 = advance_step(s, skipped=True)
    assert s2["steps_skipped"] == 1


# ── progress_bar ──────────────────────────────────────────────────────────────


def test_progress_bar_empty_at_start():
    from client_surfaces.operator_tui.snake_tutorial import progress_bar

    s = {"current_step": 0, "step_count": 10}
    assert progress_bar(s, width=10) == "░" * 10


def test_progress_bar_full_at_last_step():
    from client_surfaces.operator_tui.snake_tutorial import progress_bar

    s = {"current_step": 10, "step_count": 10}
    bar = progress_bar(s, width=10)
    assert "░" not in bar


def test_progress_bar_midpoint():
    from client_surfaces.operator_tui.snake_tutorial import progress_bar

    s = {"current_step": 5, "step_count": 10}
    bar = progress_bar(s, width=10)
    assert bar.count("█") == 5
    assert bar.count("░") == 5


# ── format_step_header ────────────────────────────────────────────────────────


def test_format_step_header_contains_step_info():
    from client_surfaces.operator_tui.snake_tutorial import format_step_header

    s = {"active": True, "current_step": 2, "step_count": 9}
    header = format_step_header(s)
    assert "Step 3/9" in header
    assert "Tutorial" in header


def test_format_step_header_empty_when_inactive():
    from client_surfaces.operator_tui.snake_tutorial import format_step_header

    assert format_step_header({"active": False}) == ""


# ── check_step_completion ─────────────────────────────────────────────────────


def test_check_step_completion_any_key_always_true():
    from client_surfaces.operator_tui.snake_tutorial import check_step_completion

    step = {"completion_event": "any_key"}
    assert check_step_completion(step, "navigation_moved") is True
    assert check_step_completion(step, "section_changed") is True


def test_check_step_completion_specific_event_matches():
    from client_surfaces.operator_tui.snake_tutorial import check_step_completion

    step = {"completion_event": "snake_activated"}
    assert check_step_completion(step, "snake_activated") is True
    assert check_step_completion(step, "any_key") is False


# ── make_step_artifact ────────────────────────────────────────────────────────


def test_make_step_artifact_structure():
    from client_surfaces.operator_tui.snake_tutorial import make_step_artifact

    ts = {"name": "intro", "steps_skipped": 1}
    step = {"id": "welcome", "title": "Welcome"}
    artifact = make_step_artifact(ts, step, operator="test-op")
    assert artifact["type"] == "tutorial_step"
    assert artifact["tutorial"] == "intro"
    assert artifact["step_id"] == "welcome"
    assert artifact["operator"] == "test-op"
    assert artifact["steps_skipped"] == 1


def test_make_completion_artifact_includes_duration():
    from client_surfaces.operator_tui.snake_tutorial import make_completion_artifact

    now = time.monotonic()
    ts = {"name": "intro", "title": "Intro", "started_at": now - 30.0, "completed_at": now, "steps_skipped": 0}
    artifact = make_completion_artifact(ts)
    assert artifact["type"] == "tutorial_complete"
    assert artifact["duration_seconds"] == pytest.approx(30.0, abs=1.0)
