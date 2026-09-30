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


@dataclass(frozen=True)
class _ShellLayout:
    """Pane geometry of the operator shell used for mouse hit-testing."""

    w: int
    body_start: int
    nav_x1: int
    nav_x2: int
    content_x1: int
    content_x2: int
    detail_x1: int
    detail_x2: int
    body_y1: int
    body_y2: int

    @classmethod
    def for_state(cls, state: OperatorState, *, width: int, height: int) -> "_ShellLayout":
        w = max(72, int(width))
        h = max(18, int(height))
        left_width = 22
        detail_width = 34
        middle_width = max(12, w - left_width - detail_width - 6)
        body_start = 10 if len(state.open_tabs) >= 2 else 9
        body_height = max(3, h - 5 - body_start)
        content_x2 = left_width + 1 + middle_width
        return cls(
            w=w,
            body_start=body_start,
            nav_x1=0,
            nav_x2=left_width - 1,
            content_x1=left_width + 2,
            content_x2=content_x2,
            detail_x1=content_x2 + 3,
            detail_x2=min(w - 1, content_x2 + 2 + detail_width),
            body_y1=body_start,
            body_y2=min(h - 3, body_start + body_height - 1),
        )

    def nav_row(self, row: int, target: RegionTarget) -> RegionRect:
        return RegionRect(x1=self.nav_x1, y1=row, x2=self.nav_x2, y2=row, target=target)

    def content_row(self, row: int, target: RegionTarget) -> RegionRect:
        return RegionRect(x1=self.content_x1, y1=row, x2=self.content_x2, y2=row, target=target)


def _pane_regions(layout: _ShellLayout, section) -> list[RegionRect]:
    def pane(pane_id: str, label: str, focus: FocusPane) -> RegionTarget:
        return RegionTarget(
            kind="pane", section_id=section.id, pane=pane_id, label=label, payload={"focus": focus.value}
        )

    body_y1, body_y2 = layout.body_y1, layout.body_y2
    return [
        RegionRect(x1=0, y1=0, x2=layout.w - 1, y2=max(0, layout.body_start - 2),
                   target=pane("header", "HEADER", FocusPane.HEADER)),
        RegionRect(x1=layout.nav_x1, y1=body_y1, x2=layout.nav_x2, y2=body_y2,
                   target=pane("nav", "NAV", FocusPane.NAVIGATION)),
        RegionRect(x1=layout.content_x1, y1=body_y1, x2=layout.content_x2, y2=body_y2,
                   target=pane("content", section.title, FocusPane.CONTENT)),
        RegionRect(x1=layout.detail_x1, y1=body_y1, x2=layout.detail_x2, y2=body_y2,
                   target=pane("detail", "DETAIL", FocusPane.DETAIL)),
    ]


@dataclass(frozen=True)
class _NavTree:
    """Grouped navigation entries shown below the active templates/audit section."""

    section_id: str
    prefix: str
    groups: list
    default_label: str


def _nav_tree_regions(
    layout: _ShellLayout, tree: _NavTree, nav_row: int, selection_index: int, regions: list[RegionRect]
) -> int:
    """Append the group and item rows of ``tree``; return the next free nav row."""
    item_index_key = f"{tree.prefix}_item_index"
    for group_name, group_rows in tree.groups:
        if nav_row > layout.body_y2:
            break
        regions.append(layout.nav_row(nav_row, RegionTarget(
            kind=f"{tree.prefix}_nav_group", section_id=tree.section_id, pane="nav", label=group_name, payload={},
        )))
        nav_row += 1
        for item_index, item in group_rows:
            if nav_row > layout.body_y2:
                break
            regions.append(layout.nav_row(nav_row, RegionTarget(
                kind=f"{tree.prefix}_nav_item",
                section_id=tree.section_id,
                pane="nav",
                label=str(item.get("title") or item.get("id") or tree.default_label),
                payload={"selected_index": selection_index, item_index_key: item_index},
            )))
            nav_row += 1
            selection_index += 1
    return nav_row


def _nav_regions(layout: _ShellLayout, state: OperatorState, trees: dict[str, _NavTree], regions: list) -> int:
    """Section rows plus the expanded tree of the active section; return the next free nav row."""
    nav_row = layout.body_y1 + 1
    for idx, nav_section in enumerate(SECTIONS):
        if nav_row > layout.body_y2:
            break
        regions.append(layout.nav_row(nav_row, RegionTarget(
            kind="section",
            section_id=nav_section.id,
            pane="nav",
            label=nav_section.title,
            payload={"section_id": nav_section.id, "selected_index": idx},
        )))
        nav_row += 1
        tree = trees.get(nav_section.id)
        if tree is not None and state.section_id == nav_section.id:
            nav_row = _nav_tree_regions(layout, tree, nav_row, len(SECTIONS), regions)
    return nav_row


def _chat_history_regions(
    layout: _ShellLayout, game: dict, section, row: int, selection_base: int, regions: list
) -> None:
    history_rows = long_message_history_rows(game)
    if not history_rows:
        return
    row += 3
    current_channel = ""
    for idx, entry in enumerate(history_rows):
        channel = str(entry.get("channel_id") or "room:main")
        if channel != current_channel:
            current_channel = channel
            if idx > 0:
                row += 1
        if row > layout.body_y2:
            break
        regions.append(layout.nav_row(row, RegionTarget(
            kind="chat_history",
            section_id=section.id,
            pane="nav",
            label=str(entry.get("preview") or entry.get("text") or "Chat History"),
            payload={"selected_index": selection_base + idx, "history_index": idx},
        )))
        row += 1


def _content_items(game: dict, payload: Any, config_mode: bool) -> Any:
    if not config_mode:
        return payload.get("items") if isinstance(payload, dict) else []
    return [
        {
            "id": str(item.get("key") or f"cfg-{idx}"),
            "title": str(item.get("label") or ""),
            "ai_snake_config_key": str(item.get("key") or ""),
            "index": idx,
        }
        for idx, item in enumerate(ai_snake_config_items(dict(game)))
    ]


def _content_item_regions(layout: _ShellLayout, section, items: list, config_mode: bool, regions: list) -> None:
    # In AI config view, content rows start after:
    # row 0 title + row 1/2 descriptions + row 3 empty spacer.
    item_row_offset = 4 if config_mode else 1
    kind = "artifact" if section.id in {"artifacts", "knowledge", "audit"} else "item"
    for idx, item in enumerate(items[:20]):
        if not isinstance(item, dict):
            continue
        row = layout.body_y1 + item_row_offset + idx
        if row > layout.body_y2:
            break
        artifact_id = str(item.get("id") or "")
        artifact_path = str(item.get("path") or item.get("file") or "")
        regions.append(layout.content_row(row, RegionTarget(
            kind=kind,
            section_id=section.id,
            pane="content",
            label=str(item.get("title") or artifact_path or artifact_id or f"item-{idx}"),
            payload={
                "selected_index": idx,
                "id": artifact_id,
                "path": artifact_path,
                "title": str(item.get("title") or ""),
                "ai_snake_config_key": str(item.get("ai_snake_config_key") or ""),
            },
        )))


def _config_combo_regions(
    layout: _ShellLayout, state: OperatorState, section, game: dict, combo: dict, items: Any, regions: list
) -> None:
    combo_key = str(combo.get("key") or "")
    filter_text = str(combo.get("filter") or "")
    options, filter_error = ai_snake_config_filter_options(dict(game), key=combo_key, regex_filter=filter_text)
    combo_row_start = layout.body_y1 + 8 + len(items) + (1 if filter_error else 0)
    for idx, option in enumerate(options[:10]):
        row = combo_row_start + idx
        if row > layout.body_y2:
            break
        regions.append(layout.content_row(row, RegionTarget(
            kind="item",
            section_id=section.id,
            pane="content",
            label=str(option),
            payload={"selected_index": int(state.selected_index), "ai_snake_combo_option_value": str(option)},
        )))


def _tab_regions(layout: _ShellLayout, state: OperatorState, section, regions: list) -> None:
    tab_y = layout.body_start - 1
    tab_pos_list = tab_positions_for_render(state, width=layout.w, y=tab_y)

    def tab_target(kind: str, label: str, payload: dict) -> RegionTarget:
        return RegionTarget(kind=kind, section_id=section.id, pane="tab", label=label, payload=payload)

    for tp in tab_pos_list:
        if tp.label_x1 <= tp.label_x2:
            regions.append(RegionRect(x1=tp.label_x1, y1=tab_y, x2=tp.label_x2, y2=tab_y,
                                      target=tab_target("tab", tp.tab_id, {"tab_id": tp.tab_id})))
        regions.append(RegionRect(x1=tp.close_x, y1=tab_y, x2=tp.close_x, y2=tab_y,
                                  target=tab_target("tab_close", tp.tab_id, {"tab_id": tp.tab_id})))
    # Overflow scroll arrows at fixed positions
    if state.tab_scroll_offset > 0:
        regions.append(RegionRect(x1=0, y1=tab_y, x2=1, y2=tab_y,
                                  target=tab_target("tab_scroll_left", "scroll_left", {})))
    if state.tab_scroll_offset + len(tab_pos_list) < len(state.open_tabs):
        arrow_x = max(2, (tab_pos_list[-1].close_x + 2) if tab_pos_list else 2)
        regions.append(RegionRect(x1=arrow_x, y1=tab_y, x2=min(layout.w - 1, arrow_x + 1), y2=tab_y,
                                  target=tab_target("tab_scroll_right", "scroll_right", {})))


def _nav_trees(state: OperatorState) -> tuple[dict[str, _NavTree], int]:
    """Expanded nav trees of the active section and the number of their flat items."""
    trees: dict[str, _NavTree] = {}
    flat_count = 0
    if state.section_id == "templates":
        templates_payload = dict((state.section_payloads or {}).get("templates") or {})
        trees["templates"] = _NavTree("templates", "template", grouped_template_items(templates_payload), "Template")
        flat_count += len(template_nav_items(templates_payload))
    if state.section_id == "audit":
        audit_payload = dict((state.section_payloads or {}).get("audit") or {})
        trees["audit"] = _NavTree("audit", "audit", grouped_audit_items(audit_payload), "Audit")
        flat_count += len(audit_nav_items(audit_payload))
    return trees, flat_count


def build_region_index(state: OperatorState, *, width: int, height: int) -> RegionIndex:
    layout = _ShellLayout.for_state(state, width=width, height=height)
    section = get_section(state.section_id)
    payload = (state.section_payloads or {}).get(section.id, {})
    regions = _pane_regions(layout, section)

    trees, flat_count = _nav_trees(state)
    nav_row = _nav_regions(layout, state, trees, regions)

    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    _chat_history_regions(layout, game, section, nav_row, len(SECTIONS) + flat_count, regions)

    config_mode = bool(game.get("ai_snake_config_open"))
    combo = dict(game.get("ai_snake_config_combo") or {})
    items = _content_items(game, payload, config_mode)
    if isinstance(items, list) and section.id not in {"kanban", "models"}:
        _content_item_regions(layout, section, items, config_mode, regions)

    if section.id in {"kanban", "models"}:
        from client_surfaces.operator_tui.dashboard_surfaces import dashboard_region_rects

        regions.extend(
            dashboard_region_rects(
                section.id,
                payload,
                x1=layout.content_x1,
                x2=layout.content_x2,
                y1=layout.body_y1,
                y2=layout.body_y2,
                selected_index=state.selected_index,
            )
        )

    if config_mode and bool(combo.get("open")):
        _config_combo_regions(layout, state, section, game, combo, items, regions)

    if len(state.open_tabs) >= 2:
        _tab_regions(layout, state, section, regions)

    return RegionIndex(regions)
