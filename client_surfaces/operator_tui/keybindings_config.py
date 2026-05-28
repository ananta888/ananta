from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

_DEFAULT_FILE = Path("config/operator_tui_keybindings.default.json")


def _resolve_config_file() -> Path:
    raw = os.environ.get("ANANTA_TUI_KEYBINDINGS_FILE", "").strip()
    if raw:
        return Path(raw)
    return _DEFAULT_FILE


@lru_cache(maxsize=2)
def _load_bindings(config_path: str) -> dict[str, dict[str, object]]:
    path = Path(config_path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = payload.get("bindings")
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        action = str(row.get("action") or "").strip()
        key = str(row.get("key") or "").strip()
        if not action or not key:
            continue
        out[action] = dict(row)
    return out


def _binding(action: str) -> dict[str, object]:
    data = _load_bindings(str(_resolve_config_file()))
    return dict(data.get(action) or {})


def key_for_action(action: str, default_key: str) -> str:
    entry = _binding(action)
    key = str(entry.get("key") or "").strip()
    return key or default_key


def display_for_action(action: str, default_display: str) -> str:
    entry = _binding(action)
    display = str(entry.get("display") or "").strip()
    return display or default_display


def label_for_action(action: str, default_label: str) -> str:
    entry = _binding(action)
    label = str(entry.get("label") or "").strip()
    return label or default_label


def shortcut_tokens_for_area(area: str) -> list[tuple[str, str]]:
    data = _load_bindings(str(_resolve_config_file()))
    tokens: list[tuple[str, str]] = []
    for entry in data.values():
        areas = entry.get("areas")
        if not isinstance(areas, list):
            continue
        if area not in {str(item) for item in areas if isinstance(item, str)}:
            continue
        display = str(entry.get("display") or "").strip()
        label = str(entry.get("label") or "").strip()
        if display and label:
            tokens.append((display, label))
    return tokens


def keybinding_conflicts() -> list[dict[str, object]]:
    data = _load_bindings(str(_resolve_config_file()))
    by_key: dict[str, list[str]] = {}
    for action, entry in data.items():
        key = str(entry.get("key") or "").strip()
        if not key:
            continue
        by_key.setdefault(key, []).append(action)
    conflicts: list[dict[str, object]] = []
    for key, actions in by_key.items():
        if len(actions) < 2:
            continue
        conflicts.append({"key": key, "actions": sorted(actions)})
    conflicts.sort(key=lambda row: str(row.get("key") or ""))
    return conflicts


def area_keybinding_conflicts(area: str) -> list[dict[str, object]]:
    data = _load_bindings(str(_resolve_config_file()))
    by_key: dict[str, list[str]] = {}
    for action, entry in data.items():
        areas = entry.get("areas")
        if not isinstance(areas, list):
            continue
        if area not in {str(a) for a in areas}:
            continue
        key = str(entry.get("key") or "").strip()
        if not key:
            continue
        by_key.setdefault(key, []).append(action)
    conflicts: list[dict[str, object]] = []
    for key, actions in by_key.items():
        if len(actions) < 2:
            continue
        conflicts.append({"key": key, "area": area, "actions": sorted(actions)})
    conflicts.sort(key=lambda row: str(row.get("key") or ""))
    return conflicts


_CTRL_M_KEYS = frozenset({"c-m", "ctrl+m", "ctrl-m"})


def ctrl_m_is_unsafe() -> bool:
    """Return True when Ctrl+M is configured — it is typically indistinguishable from Enter."""
    data = _load_bindings(str(_resolve_config_file()))
    for entry in data.values():
        key = str(entry.get("key") or "").strip().lower()
        if key in _CTRL_M_KEYS:
            return True
    return False


def ctrl_m_binding_diagnostics() -> dict[str, object] | None:
    """Return the binding info for any Ctrl+M key, or None if not configured."""
    data = _load_bindings(str(_resolve_config_file()))
    for action, entry in data.items():
        key = str(entry.get("key") or "").strip().lower()
        if key in _CTRL_M_KEYS:
            return {
                "action": action,
                "key": entry.get("key"),
                "display": entry.get("display"),
                "label": entry.get("label"),
                "unsafe_reason": "Ctrl+M is commonly indistinguishable from Enter/carriage-return in terminal input stacks.",
                "recommended_alternative": key_for_action("toggle_visual_view_switcher_overlay", "f8"),
            }
    return None
