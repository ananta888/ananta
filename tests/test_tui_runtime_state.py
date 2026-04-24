from __future__ import annotations

from client_surfaces.tui_runtime.ananta_tui.browser_fallback import (
    build_browser_fallback_snapshot,
    build_object_browser_url,
)
from client_surfaces.tui_runtime.ananta_tui.state import TuiViewState


def test_tui_view_state_sanitizes_stale_selection() -> None:
    state = TuiViewState(current_section="Tasks").with_selection(
        goal_id="G-1",
        task_id="T-1",
        artifact_id="A-1",
        collection_id="KC-1",
        template_id="TPL-1",
    )
    sanitized = state.sanitize_selection(
        goal_ids={"G-1"},
        task_ids={"T-2"},
        artifact_ids=set(),
        collection_ids={"KC-2"},
        template_ids=set(),
    )

    assert sanitized.selected_goal_id == "G-1"
    assert sanitized.selected_task_id is None
    assert sanitized.selected_artifact_id is None
    assert sanitized.selected_collection_id is None
    assert sanitized.selected_template_id is None


def test_tui_view_state_compact_mode_switches_for_small_width() -> None:
    state = TuiViewState().with_terminal_width(90)
    assert state.compact_mode is True
    assert state.with_terminal_width(120).compact_mode is False


def test_browser_fallback_snapshot_contains_selected_links() -> None:
    state = TuiViewState().with_selection(goal_id="G-1", task_id="T-1", artifact_id="A-1")
    snapshot = build_browser_fallback_snapshot("http://localhost:8080", state)
    links = snapshot["links"]
    assert links["selected_goal"] == "http://localhost:8080/goals/G-1"
    assert links["selected_task"] == "http://localhost:8080/tasks/T-1"
    assert links["selected_artifact"] == "http://localhost:8080/artifacts/A-1"


def test_browser_fallback_unknown_object_type_returns_none() -> None:
    assert build_object_browser_url("http://localhost:8080", "unknown", "X-1") is None
