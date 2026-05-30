from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from client_surfaces.operator_tui.chat_long_message import long_message_history_rows
from client_surfaces.operator_tui.models import FocusPane, OperatorState
from client_surfaces.operator_tui.audit_nav import grouped_audit_items, audit_nav_items
from client_surfaces.operator_tui.ai_snake_config_view import ai_snake_config_filter_options, ai_snake_config_items
from client_surfaces.operator_tui.sections import SECTIONS, get_section
from client_surfaces.operator_tui.template_nav import grouped_template_items, template_nav_items
from client_surfaces.operator_tui.tab_manager import tab_positions_for_render


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
    body_start = 10 if len(state.open_tabs) >= 2 else 9
    body_height = max(3, h - 5 - body_start)
    nav_x1, nav_x2 = 0, left_width - 1
    content_x1, content_x2 = left_width + 2, left_width + 1 + middle_width
    detail_x1, detail_x2 = content_x2 + 3, min(w - 1, content_x2 + 2 + detail_width)
    body_y1, body_y2 = body_start, min(h - 3, body_start + body_height - 1)
    section = get_section(state.section_id)
    payload = (state.section_payloads or {}).get(section.id, {})

    regions: list[RegionRect] = [
        RegionRect(
            x1=0,
            y1=0,
            x2=w - 1,
            y2=max(0, body_start - 2),
            target=RegionTarget(kind="pane", section_id=section.id, pane="header", label="HEADER", payload={"focus": FocusPane.HEADER.value}),
        ),
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

    templates_payload = dict((state.section_payloads or {}).get("templates") or {})
    template_groups = grouped_template_items(templates_payload) if state.section_id == "templates" else []
    template_flat = template_nav_items(templates_payload) if state.section_id == "templates" else []
    audit_payload = dict((state.section_payloads or {}).get("audit") or {})
    audit_groups = grouped_audit_items(audit_payload) if state.section_id == "audit" else []
    audit_flat = audit_nav_items(audit_payload) if state.section_id == "audit" else []
    nav_row = body_y1 + 1
    template_selection_index = len(SECTIONS)
    audit_selection_index = len(SECTIONS)
    for idx, nav_section in enumerate(SECTIONS):
        if nav_row > body_y2:
            break
        regions.append(
            RegionRect(
                x1=nav_x1,
                y1=nav_row,
                x2=nav_x2,
                y2=nav_row,
                target=RegionTarget(
                    kind="section",
                    section_id=nav_section.id,
                    pane="nav",
                    label=nav_section.title,
                    payload={"section_id": nav_section.id, "selected_index": idx},
                ),
            )
        )
        nav_row += 1
        if nav_section.id == "templates" and state.section_id == "templates":
            for group_name, group_rows in template_groups:
                if nav_row > body_y2:
                    break
                regions.append(
                    RegionRect(
                        x1=nav_x1,
                        y1=nav_row,
                        x2=nav_x2,
                        y2=nav_row,
                        target=RegionTarget(
                            kind="template_nav_group",
                            section_id="templates",
                            pane="nav",
                            label=group_name,
                            payload={},
                        ),
                    )
                )
                nav_row += 1
                for item_index, item in group_rows:
                    if nav_row > body_y2:
                        break
                    regions.append(
                        RegionRect(
                            x1=nav_x1,
                            y1=nav_row,
                            x2=nav_x2,
                            y2=nav_row,
                            target=RegionTarget(
                                kind="template_nav_item",
                                section_id="templates",
                                pane="nav",
                                label=str(item.get("title") or item.get("id") or "Template"),
                                payload={"selected_index": template_selection_index, "template_item_index": item_index},
                            ),
                        )
                    )
                    nav_row += 1
                    template_selection_index += 1
        if nav_section.id == "audit" and state.section_id == "audit":
            for group_name, group_rows in audit_groups:
                if nav_row > body_y2:
                    break
                regions.append(
                    RegionRect(
                        x1=nav_x1,
                        y1=nav_row,
                        x2=nav_x2,
                        y2=nav_row,
                        target=RegionTarget(
                            kind="audit_nav_group",
                            section_id="audit",
                            pane="nav",
                            label=group_name,
                            payload={},
                        ),
                    )
                )
                nav_row += 1
                for item_index, item in group_rows:
                    if nav_row > body_y2:
                        break
                    regions.append(
                        RegionRect(
                            x1=nav_x1,
                            y1=nav_row,
                            x2=nav_x2,
                            y2=nav_row,
                            target=RegionTarget(
                                kind="audit_nav_item",
                                section_id="audit",
                                pane="nav",
                                label=str(item.get("title") or item.get("id") or "Audit"),
                                payload={"selected_index": audit_selection_index, "audit_item_index": item_index},
                            ),
                        )
                    )
                    nav_row += 1
                    audit_selection_index += 1

    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    history_rows = long_message_history_rows(game)
    row = nav_row
    if history_rows:
        row += 3
        current_channel = ""
        for idx, entry in enumerate(history_rows):
            channel = str(entry.get("channel_id") or "room:main")
            if channel != current_channel:
                current_channel = channel
                if idx > 0:
                    row += 1
            if row > body_y2:
                break
            regions.append(
                RegionRect(
                    x1=nav_x1,
                    y1=row,
                    x2=nav_x2,
                    y2=row,
                    target=RegionTarget(
                        kind="chat_history",
                        section_id=section.id,
                        pane="nav",
                        label=str(entry.get("preview") or entry.get("text") or "Chat History"),
                        payload={"selected_index": len(SECTIONS) + len(template_flat) + len(audit_flat) + idx, "history_index": idx},
                    ),
                )
            )
            row += 1

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

    if len(state.open_tabs) >= 2:
        tab_y = body_start - 1
        tab_pos_list = tab_positions_for_render(state, width=w, y=tab_y)
        for tp in tab_pos_list:
            if tp.label_x1 <= tp.label_x2:
                regions.append(RegionRect(
                    x1=tp.label_x1, y1=tab_y, x2=tp.label_x2, y2=tab_y,
                    target=RegionTarget(kind="tab", section_id=section.id, pane="tab",
                                       label=tp.tab_id, payload={"tab_id": tp.tab_id}),
                ))
            regions.append(RegionRect(
                x1=tp.close_x, y1=tab_y, x2=tp.close_x, y2=tab_y,
                target=RegionTarget(kind="tab_close", section_id=section.id, pane="tab",
                                    label=tp.tab_id, payload={"tab_id": tp.tab_id}),
            ))
        # Overflow scroll arrows at fixed positions
        if state.tab_scroll_offset > 0:
            regions.append(RegionRect(
                x1=0, y1=tab_y, x2=1, y2=tab_y,
                target=RegionTarget(kind="tab_scroll_left", section_id=section.id, pane="tab",
                                    label="scroll_left", payload={}),
            ))
        offset = state.tab_scroll_offset
        if offset + len(tab_pos_list) < len(state.open_tabs):
            arrow_x = max(2, (tab_pos_list[-1].close_x + 2) if tab_pos_list else 2)
            regions.append(RegionRect(
                x1=arrow_x, y1=tab_y, x2=min(w - 1, arrow_x + 1), y2=tab_y,
                target=RegionTarget(kind="tab_scroll_right", section_id=section.id, pane="tab",
                                    label="scroll_right", payload={}),
            ))

    return RegionIndex(regions)
