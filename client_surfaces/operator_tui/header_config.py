from __future__ import annotations

from client_surfaces.operator_tui.models import OperatorMode, OperatorState

CONFIG_ITEMS: tuple[str, ...] = ("mode", "endpoint", "auth", "3d_anim", "color")

CONFIG_LABELS: dict[str, str] = {
    "mode":    "Mode    ",
    "endpoint":"Endpoint",
    "auth":    "Auth    ",
    "3d_anim": "3D anim ",
    "color":   "Color   ",
}

_CYCLEABLE = {"mode", "3d_anim", "color"}


def config_value(state: OperatorState, key: str) -> str:
    if key == "mode":
        return state.mode.value
    if key == "endpoint":
        url = state.endpoint or "—"
        return url if len(url) <= 26 else "…" + url[-25:]
    if key == "auth":
        return state.auth_state or "—"
    if key == "3d_anim":
        return "off" if (state.terminal_graphics or {}).get("no_3d") else "on"
    if key == "color":
        return "off" if (state.terminal_graphics or {}).get("no_color") else "on"
    return "?"


def is_cycleable(key: str) -> bool:
    return key in _CYCLEABLE


def cycle_value(state: OperatorState, key: str) -> OperatorState:
    if key == "mode":
        modes = [OperatorMode.NORMAL, OperatorMode.INSPECT, OperatorMode.EDIT]
        cur = state.mode if state.mode in modes else OperatorMode.NORMAL
        return state.with_updates(mode=modes[(modes.index(cur) + 1) % len(modes)])
    if key == "3d_anim":
        tg = dict(state.terminal_graphics or {})
        tg["no_3d"] = not tg.get("no_3d", False)
        return state.with_updates(terminal_graphics=tg)
    if key == "color":
        tg = dict(state.terminal_graphics or {})
        tg["no_color"] = not tg.get("no_color", False)
        return state.with_updates(terminal_graphics=tg)
    return state
