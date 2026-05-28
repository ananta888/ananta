from __future__ import annotations

from client_surfaces.operator_tui.models import OperatorState
from client_surfaces.operator_tui.region_index import build_region_index


def test_artifact_row_hit_returns_artifact_target() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="artifacts",
        section_payloads={
            "artifacts": {
                "items": [
                    {"id": "a-1", "title": "Cast", "path": "tests/output/operator_tui_splash.cast"},
                ]
            }
        },
    )
    hit = build_region_index(state, width=120, height=32).get_target_at(28, 10)
    assert hit is not None
    assert hit.kind in {"artifact", "item"}
    assert hit.payload.get("path") == "tests/output/operator_tui_splash.cast"


def test_empty_space_returns_no_target() -> None:
    state = OperatorState(endpoint="http://localhost:5000", section_id="artifacts")
    hit = build_region_index(state, width=120, height=32).get_target_at(119, 0)
    assert hit is None


def test_resize_rebuilds_hit_map() -> None:
    state = OperatorState(endpoint="http://localhost:5000", section_id="dashboard")
    hit_small = build_region_index(state, width=80, height=20).get_target_at(10, 12)
    hit_large = build_region_index(state, width=160, height=40).get_target_at(10, 12)
    assert (hit_small is not None) or (hit_large is not None)


def test_ai_snake_config_rows_align_with_rendered_content() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="tasks",
        header_logo_game={"ai_snake_config_open": True, "tutorial_mode": True},
    )
    # body_start=9; first config item is rendered at content row offset +4 => y=13
    hit = build_region_index(state, width=120, height=32).get_target_at(28, 13)
    assert hit is not None
    assert hit.pane == "content"
    assert hit.payload.get("selected_index") == 0
    assert hit.payload.get("ai_snake_config_key") == "visual_enabled"


def test_ai_snake_config_combobox_option_rows_are_clickable() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="tasks",
        header_logo_game={
            "ai_snake_config_open": True,
            "ai_snake_config_combo": {
                "open": True,
                "key": "visual_enabled",
                "filter": "",
                "filter_cursor": 0,
                "selected_option": 0,
            },
        },
    )
    # body_start=9; options begin at row index 8 + len(items=8) => y=25 for first option.
    hit = build_region_index(state, width=120, height=40).get_target_at(28, 25)
    assert hit is not None
    assert hit.pane == "content"
    assert str(hit.payload.get("ai_snake_combo_option_value") or "") in {"AN", "AUS"}
