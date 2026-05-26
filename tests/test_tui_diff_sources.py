from __future__ import annotations

import pytest

from client_surfaces.operator_tui.diff.diff_sources import (
    build_current_diff_source_ref,
    build_diff_panel_config,
    build_diff_source_ref,
    validate_diff_panel_config,
    validate_diff_source_ref,
)
from client_surfaces.operator_tui.diff.three_way_diff_state import build_current_diff_three_panel_session


def test_diff_source_ref_schema_allows_supported_source_kinds() -> None:
    kinds = [
        "working_tree",
        "git_ref",
        "git_diff",
        "file_path",
        "artifact_ref",
        "goal_output_artifact",
        "task_output",
        "snapshot",
        "inline_text",
    ]
    for kind in kinds:
        payload = build_diff_source_ref(
            source_ref_id=f"src-{kind}",
            source_kind=kind,
            display_name=f"Source {kind}",
            locator={"path": "demo.txt"},
        )
        assert validate_diff_source_ref(payload) == []


def test_diff_panel_config_accepts_all_render_modes() -> None:
    modes = ["unified", "side_by_side", "summary", "files_only", "hunks_only", "ai_chat", "ai_review"]
    for mode in modes:
        payload = build_diff_panel_config(panel_id="A", render_mode=mode, filters={})
        assert validate_diff_panel_config(payload) == []


def test_current_diff_preset_and_three_panel_defaults() -> None:
    session = build_current_diff_three_panel_session(session_id="s-1")
    panel_a = next(item for item in session["panels"] if item["panel_id"] == "A")
    panel_b = next(item for item in session["panels"] if item["panel_id"] == "B")
    panel_c = next(item for item in session["panels"] if item["panel_id"] == "C")
    assert panel_a["source_left"]["source_kind"] == "git_diff"
    assert panel_b["source_left"]["source_kind"] == "git_diff"
    assert panel_a["source_left"]["source_ref_id"] == panel_b["source_left"]["source_ref_id"]
    assert panel_a["render_mode"] == "unified"
    assert panel_b["render_mode"] == "summary"
    assert panel_c["panel_type"] == "ai_review"
    assert panel_c["render_mode"] == "ai_review"


def test_current_diff_source_ref_validation_rejects_invalid_kind() -> None:
    with pytest.raises(ValueError):
        build_diff_source_ref(
            source_ref_id="bad",
            source_kind="not_supported",
            display_name="Bad",
            locator={},
        )


def test_current_diff_builder_sets_head_to_working_tree() -> None:
    payload = build_current_diff_source_ref()
    assert payload["source_kind"] == "git_diff"
    assert payload["locator"]["base_ref"] == "HEAD"
    assert payload["locator"]["target"] == "working_tree"


def test_diff_panel_config_rejects_invalid_panel_id() -> None:
    with pytest.raises(ValueError):
        build_diff_panel_config(panel_id="Z", render_mode="unified", filters={})


def test_diff_panel_config_rejects_invalid_render_mode() -> None:
    with pytest.raises(ValueError):
        build_diff_panel_config(panel_id="A", render_mode="invalid_mode", filters={})


def test_current_diff_source_ref_includes_path_filter() -> None:
    payload = build_current_diff_source_ref(path_filter="src/")
    assert payload["locator"]["path_filter"] == "src/"
