from __future__ import annotations

from dataclasses import replace
from textwrap import shorten
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from client_surfaces.operator_tui.models import OperatorState, TuiTab


def tab_label_for_section(section_id: str) -> str:
    from client_surfaces.operator_tui.sections import get_section
    return get_section(section_id).title


def tab_label_for_chat_preview(text: str) -> str:
    cleaned = str(text or "").replace("\n", " ").strip()
    return shorten(cleaned, width=10, placeholder="…")


def find_tab(
    state: "OperatorState",
    *,
    section_id: str | None = None,
    tab_id: str | None = None,
    kind: str | None = None,
) -> "TuiTab | None":
    for tab in state.open_tabs:
        if tab_id is not None and tab.id == tab_id:
            return tab
        if section_id is not None and kind is not None:
            if tab.section_id == section_id and tab.kind == kind:
                return tab
        elif section_id is not None and tab.section_id == section_id:
            return tab
    return None


def open_or_activate_tab(
    state: "OperatorState",
    *,
    section_id: str,
    kind: str,
    label: str,
    viewport_state: dict[str, Any] | None = None,
) -> "OperatorState":
    from client_surfaces.operator_tui.models import TuiTab

    existing = find_tab(state, section_id=section_id, kind=kind)
    if existing is not None:
        if state.active_tab_id == existing.id:
            return state
        return state.with_updates(active_tab_id=existing.id)

    if kind == "chat_viewport":
        import time
        tab_id = f"chat:{int(time.time() * 1000) % 100000000}"
    else:
        tab_id = f"section:{section_id}"

    new_tab = TuiTab(id=tab_id, kind=kind, section_id=section_id, label=label, viewport_state=viewport_state)  # type: ignore[arg-type]
    new_tabs = state.open_tabs + (new_tab,)
    new_scroll = _scroll_to_show(state.tab_scroll_offset, len(new_tabs) - 1)
    return state.with_updates(open_tabs=new_tabs, active_tab_id=tab_id, tab_scroll_offset=new_scroll)


def close_tab(state: "OperatorState", tab_id: str) -> "OperatorState":
    from client_surfaces.operator_tui.models import TuiTab

    tabs = state.open_tabs
    idx = next((i for i, t in enumerate(tabs) if t.id == tab_id), None)
    if idx is None:
        return state

    new_tabs = tabs[:idx] + tabs[idx + 1:]

    if not new_tabs:
        dashboard = TuiTab(id="section:dashboard", kind="section", section_id="dashboard", label="Dashboard")  # type: ignore[arg-type]
        new_tabs = (dashboard,)
        return state.with_updates(open_tabs=new_tabs, active_tab_id="section:dashboard", section_id="dashboard", tab_scroll_offset=0)

    if state.active_tab_id == tab_id:
        next_idx = max(0, idx - 1)
        next_tab = new_tabs[next_idx]
        new_scroll = _scroll_to_show(state.tab_scroll_offset, next_idx)
        return state.with_updates(
            open_tabs=new_tabs,
            active_tab_id=next_tab.id,
            section_id=next_tab.section_id,
            tab_scroll_offset=new_scroll,
        )

    return state.with_updates(open_tabs=new_tabs)


def activate_tab(
    state: "OperatorState",
    tab_id: str,
    *,
    game: dict[str, Any] | None = None,
) -> tuple["OperatorState", dict[str, Any]]:
    tab = find_tab(state, tab_id=tab_id)
    if tab is None:
        return state, dict(game or {})

    game_out = dict(game or {})
    new_state = state.with_updates(active_tab_id=tab_id, section_id=tab.section_id)

    if tab.kind == "chat_viewport":
        game_out["visual_viewport_enabled"] = True
        game_out["visual_viewport"] = {"enabled": True}
        vs = tab.viewport_state or {}
        scroll = int(vs.get("scroll_offset") or 0)
        game_out["scroll_offset_center_viewport"] = scroll
    else:
        game_out["visual_viewport_enabled"] = False
        game_out["visual_viewport"] = {"enabled": False}

    idx = next((i for i, t in enumerate(state.open_tabs) if t.id == tab_id), 0)
    new_scroll = _scroll_to_show(state.tab_scroll_offset, idx)
    new_state = new_state.with_updates(tab_scroll_offset=new_scroll)
    return new_state, game_out


def save_scroll_to_active_tab(state: "OperatorState", scroll_offset: int) -> "OperatorState":
    """Persist the current scroll position into the active chat_viewport tab's viewport_state."""
    if not state.active_tab_id:
        return state
    tabs = list(state.open_tabs)
    for i, tab in enumerate(tabs):
        if tab.id == state.active_tab_id and tab.kind == "chat_viewport":
            vs = dict(tab.viewport_state or {})
            vs["scroll_offset"] = scroll_offset
            tabs[i] = replace(tab, viewport_state=vs)
            return state.with_updates(open_tabs=tuple(tabs))
    return state


# ---------------------------------------------------------------------------
# Tab-bar rendering geometry — shared by renderer.py and region_index.py
# ---------------------------------------------------------------------------

class TabPosition:
    __slots__ = ("tab_id", "label_x1", "label_x2", "close_x", "y")

    def __init__(self, tab_id: str, label_x1: int, label_x2: int, close_x: int, y: int) -> None:
        self.tab_id = tab_id
        self.label_x1 = label_x1
        self.label_x2 = label_x2
        self.close_x = close_x
        self.y = y


def tab_positions_for_render(state: "OperatorState", width: int, *, y: int = 9) -> list[TabPosition]:
    """Compute pixel-exact x-positions for each visible tab. Same logic as _tab_bar_line()."""
    tabs = state.open_tabs
    if len(tabs) < 2:
        return []

    offset = max(0, state.tab_scroll_offset)
    visible = tabs[offset:]

    positions: list[TabPosition] = []
    x = 0
    overflow_left = offset > 0
    overflow_right = False

    if overflow_left:
        x += 2  # '‹ '

    for i, tab in enumerate(visible):
        seg = f" {tab.label} × "
        seg_w = len(seg)
        close_x = x + seg_w - 2  # position of '×'

        if x + seg_w + (1 if i < len(visible) - 1 else 0) > width - 1:
            overflow_right = True
            break

        positions.append(TabPosition(
            tab_id=tab.id,
            label_x1=x,
            label_x2=close_x - 1,
            close_x=close_x,
            y=y,
        ))
        x += seg_w
        if i < len(visible) - 1:
            x += 1  # '│' separator

    _ = overflow_right  # used by renderer, not needed here
    return positions


def _scroll_to_show(current_offset: int, tab_idx: int) -> int:
    """Ensure tab at tab_idx is >= offset (simple left-edge alignment)."""
    if tab_idx < current_offset:
        return tab_idx
    return current_offset
