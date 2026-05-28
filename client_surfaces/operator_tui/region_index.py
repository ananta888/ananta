from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from client_surfaces.operator_tui.models import FocusPane, OperatorState
from client_surfaces.operator_tui.ai_snake_config_view import ai_snake_config_filter_options, ai_snake_config_items
from client_surfaces.operator_tui.sections import SECTIONS, get_section


@dataclass(frozen=True)
class RegionTarget:
    kind: str
    section_id: str
    pane: str
    label: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class RegionRect:
    x1: int
    y1: int
    x2: int
    y2: int
    target: RegionTarget

    def contains(self, x: int, y: int) -> bool:
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2


class RegionIndex:
    def __init__(self, regions: list[RegionRect]) -> None:
        self._regions = list(regions)

    def get_target_at(self, x: int, y: int) -> RegionTarget | None:
        for region in reversed(self._regions):
            if region.contains(x, y):
                return region.target
        return None


def build_region_index(state: OperatorState, *, width: int, height: int) -> RegionIndex:
    w = max(72, int(width))
    h = max(18, int(height))
    left_width = 22
    detail_width = 34
    middle_width = max(12, w - left_width - detail_width - 6)
    body_start = 9
    body_height = max(3, h - 5 - body_start)
    nav_x1, nav_x2 = 0, left_width - 1
    content_x1, content_x2 = left_width + 2, left_width + 1 + middle_width
    detail_x1, detail_x2 = content_x2 + 3, min(w - 1, content_x2 + 2 + detail_width)
    body_y1, body_y2 = body_start, min(h - 3, body_start + body_height - 1)
    section = get_section(state.section_id)
    payload = (state.section_payloads or {}).get(section.id, {})

    regions: list[RegionRect] = [
        RegionRect(
            x1=nav_x1,
            y1=body_y1,
            x2=nav_x2,
            y2=body_y2,
            target=RegionTarget(kind="pane", section_id=section.id, pane="nav", label="NAV", payload={"focus": FocusPane.NAVIGATION.value}),
        ),
        RegionRect(
            x1=content_x1,
            y1=body_y1,
            x2=content_x2,
            y2=body_y2,
            target=RegionTarget(kind="pane", section_id=section.id, pane="content", label=section.title, payload={"focus": FocusPane.CONTENT.value}),
        ),
        RegionRect(
            x1=detail_x1,
            y1=body_y1,
            x2=detail_x2,
            y2=body_y2,
            target=RegionTarget(kind="pane", section_id=section.id, pane="detail", label="DETAIL", payload={"focus": FocusPane.DETAIL.value}),
        ),
    ]

    for idx, nav_section in enumerate(SECTIONS):
        row = body_y1 + 1 + idx
        if row > body_y2:
            break
        regions.append(
            RegionRect(
                x1=nav_x1,
                y1=row,
                x2=nav_x2,
                y2=row,
                target=RegionTarget(
                    kind="section",
                    section_id=nav_section.id,
                    pane="nav",
                    label=nav_section.title,
                    payload={"section_id": nav_section.id, "selected_index": idx},
                ),
            )
        )

    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    config_mode = bool(game.get("ai_snake_config_open"))
    combo = dict(game.get("ai_snake_config_combo") or {})
    combo_open = config_mode and bool(combo.get("open"))
    if config_mode:
        items = [
            {"id": str(item.get("key") or f"cfg-{idx}"), "title": str(item.get("label") or ""), "ai_snake_config_key": str(item.get("key") or ""), "index": idx}
            for idx, item in enumerate(ai_snake_config_items(dict(game)))
        ]
    else:
        items = payload.get("items") if isinstance(payload, dict) else []
    # In AI config view, content rows start after:
    # row 0 title + row 1/2 descriptions + row 3 empty spacer.
    item_row_offset = 4 if config_mode else 1
    if isinstance(items, list):
        for idx, item in enumerate(items[:20]):
            if not isinstance(item, dict):
                continue
            row = body_y1 + item_row_offset + idx
            if row > body_y2:
                break
            artifact_id = str(item.get("id") or "")
            artifact_path = str(item.get("path") or item.get("file") or "")
            label = str(item.get("title") or artifact_path or artifact_id or f"item-{idx}")
            kind = "artifact" if section.id in {"artifacts", "knowledge", "audit"} else "item"
            regions.append(
                RegionRect(
                    x1=content_x1,
                    y1=row,
                    x2=content_x2,
                    y2=row,
                    target=RegionTarget(
                        kind=kind,
                        section_id=section.id,
                        pane="content",
                        label=label,
                        payload={
                            "selected_index": idx,
                            "id": artifact_id,
                            "path": artifact_path,
                            "title": str(item.get("title") or ""),
                            "ai_snake_config_key": str(item.get("ai_snake_config_key") or ""),
                        },
                    ),
                )
            )

    if combo_open:
        combo_key = str(combo.get("key") or "")
        filter_text = str(combo.get("filter") or "")
        options, filter_error = ai_snake_config_filter_options(dict(game), key=combo_key, regex_filter=filter_text)
        combo_row_start = body_y1 + 8 + len(items) + (1 if filter_error else 0)
        for idx, option in enumerate(options[:10]):
            row = combo_row_start + idx
            if row > body_y2:
                break
            regions.append(
                RegionRect(
                    x1=content_x1,
                    y1=row,
                    x2=content_x2,
                    y2=row,
                    target=RegionTarget(
                        kind="item",
                        section_id=section.id,
                        pane="content",
                        label=str(option),
                        payload={
                            "selected_index": int(state.selected_index),
                            "ai_snake_combo_option_value": str(option),
                        },
                    ),
                )
            )

    return RegionIndex(regions)
