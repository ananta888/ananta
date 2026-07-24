from __future__ import annotations

import pytest

from client_surfaces.operator_tui import sections
from client_surfaces.operator_tui.dashboard_surfaces import (
    KanbanContentPlugin,
    dashboard_region_rects,
    normalise_board,
)
from client_surfaces.operator_tui.kanban_viewport import (
    plan_kanban_viewport,
)
from client_surfaces.operator_tui.models import (
    FocusPane,
    OperatorState,
    PanelState,
)
from client_surfaces.operator_tui.region_index import build_region_index
from client_surfaces.operator_tui.renderer import render_operator_shell


def _payload() -> dict:
    return normalise_board(
        {
            "revision": 1,
            "columns": [
                {
                    "id": f"c{column}",
                    "title": f"C{column}",
                    "tasks": [
                        {
                            "id": f"K{column * 100 + row:03d}",
                            "title": f"K{column * 100 + row:03d}",
                            "status": f"c{column}",
                            "priority": "P0",
                        }
                        for row in range(100)
                    ],
                }
                for column in range(10)
            ],
        }
    )


@pytest.mark.parametrize(
    ("width", "height", "capacity"),
    [(18, 9, 8), (58, 15, 14), (98, 25, 24)],
)
def test_list_viewport_projects_only_visible_cards_and_keeps_selection(
    width: int,
    height: int,
    capacity: int,
) -> None:
    plan = plan_kanban_viewport(
        _payload(),
        width=width,
        height=height,
        selected_index=999,
    )

    assert plan.layout == "list"
    assert plan.card_row_capacity == capacity
    assert len(plan.list_slots) == capacity
    assert plan.list_slots[-1].flat_index == 999
    assert all(slot.flat_index >= 1000 - capacity for slot in plan.list_slots)


@pytest.mark.parametrize(
    ("width", "height"),
    [(80, 24), (120, 30), (160, 40)],
)
def test_shell_and_hit_map_keep_global_selection_visible_after_resize(
    width: int,
    height: int,
    monkeypatch,
) -> None:
    enabled_sections = sections.dashboard_sections(
        {"ANANTA_TUI_KANBAN_ENABLED": "1"}
    )
    monkeypatch.setattr(
        sections,
        "SECTIONS",
        sections.SECTIONS
        + tuple(
            section
            for section in enabled_sections
            if section.id not in {current.id for current in sections.SECTIONS}
        ),
    )
    payload = _payload()
    state = OperatorState(
        endpoint="",
        section_id="kanban",
        focus=FocusPane.CONTENT,
        selected_index=999,
        panel_states={"kanban": PanelState.HEALTHY},
        section_payloads={"kanban": payload},
        terminal_graphics={"no_3d": True},
    )

    rendered = render_operator_shell(
        state,
        width=width,
        height=height,
        splash=None,
    )
    index = build_region_index(state, width=width, height=height)
    kanban_targets = [
        region.target
        for region in index._regions
        if region.target.section_id == "kanban"
    ]

    assert "K999" in rendered
    assert any(
        target.kind == "dashboard_item"
        and target.payload.get("selected_index") == 999
        for target in kanban_targets
    )
    assert not any(target.kind == "item" for target in kanban_targets)


def test_wide_viewport_pages_columns_and_regions_with_global_indices() -> None:
    payload = _payload()
    plugin = KanbanContentPlugin()

    lines = plugin.render(payload, width=120, height=8, selected=999)
    regions = dashboard_region_rects(
        "kanban",
        payload,
        x1=0,
        x2=119,
        y1=0,
        y2=8,
        selected_index=999,
    )

    assert "C8" in lines[1]
    assert "C9" in lines[1]
    assert any("K999" in line for line in lines)
    assert any(
        region.target.payload.get("selected_index") == 999
        for region in regions
    )
    assert all(0 <= region.x1 <= region.x2 <= 119 for region in regions)
