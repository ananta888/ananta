from __future__ import annotations

from client_surfaces.operator_tui.models import PanelState, Theme


DEFAULT_THEME = Theme(
    name="operator-dense-dark",
    selected_prefix=">",
    idle_prefix=" ",
    focused_open="[",
    focused_close="]",
    muted_prefix=".",
    warning_prefix="!",
)


def state_label(state: PanelState | str | None) -> str:
    value = state.value if isinstance(state, PanelState) else str(state or "unknown")
    if value == PanelState.HEALTHY.value:
        return "ok"
    if value == PanelState.LOADING.value:
        return "loading"
    if value == PanelState.EMPTY.value:
        return "empty"
    if value == PanelState.UNAUTHORIZED.value:
        return "auth"
    if value == PanelState.DEGRADED.value:
        return "degraded"
    return "unknown"


def state_prefix(state: PanelState | str | None) -> str:
    label = state_label(state)
    if label in {"degraded", "auth"}:
        return DEFAULT_THEME.warning_prefix
    if label in {"loading", "empty", "unknown"}:
        return DEFAULT_THEME.muted_prefix
    return " "
