from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Sequence

from client_surfaces.common.profile_auth import contains_secret_key
from client_surfaces.common.types import ClientResponse
from client_surfaces.tui_runtime.ananta_tui.surface_map import build_hub_api_surface_map

_SAFE_CONFIG_PATHS = {
    "runtime_profile",
    "governance_mode",
    "goal_workflow_enabled",
    "persisted_plans_enabled",
    "feature_flags.goal_workflow_enabled",
    "feature_flags.persisted_plans_enabled",
}


def _safe_dict(payload: Any) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


def _safe_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return [item for item in payload["items"] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _empty_response(data: Any = None) -> ClientResponse:
    return ClientResponse(ok=True, status_code=200, state="healthy", data=data, error=None, retriable=False)


def _parse_scalar(raw_value: str) -> Any:
    text = raw_value.strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered == "null":
        return None
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _assign_path(target: dict[str, Any], dotted_path: str, value: Any) -> None:
    cursor = target
    parts = [part for part in dotted_path.split(".") if part]
    for part in parts[:-1]:
        next_value = cursor.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            cursor[part] = next_value
        cursor = next_value
    if parts:
        cursor[parts[-1]] = value


def _flatten(payload: dict[str, Any], parent: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        full_key = f"{parent}.{key}" if parent else str(key)
        if isinstance(value, dict):
            out.update(_flatten(value, full_key))
        else:
            out[full_key] = value
    return out


def _merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _parse_safe_config_edits(raw_edits: Sequence[str]) -> tuple[dict[str, Any], list[str]]:
    patch: dict[str, Any] = {}
    errors: list[str] = []
    for entry in raw_edits:
        text = str(entry or "").strip()
        if not text or "=" not in text:
            errors.append(f"invalid_edit_format:{text}")
            continue
        key, raw_value = text.split("=", 1)
        dotted_key = key.strip()
        if dotted_key not in _SAFE_CONFIG_PATHS:
            errors.append(f"unsafe_key:{dotted_key}")
            continue
        if contains_secret_key(dotted_key):
            errors.append(f"secret_like_key_blocked:{dotted_key}")
            continue
        _assign_path(patch, dotted_key, _parse_scalar(raw_value))
    return patch, errors


def _render_api_map_summary() -> str:
    payload = build_hub_api_surface_map()
    classifications = Counter(
        item.get("classification")
        for methods in payload.get("by_section", {}).values()
        for item in methods
        if isinstance(item, dict)
    )
    lines = ["[API-MAP]"]
    lines.append(f"sections={len(payload.get('sections') or [])}")
    lines.append(f"methods={sum(classifications.values())}")
    lines.append(
        (
            f"class_tui_mvp={classifications.get('tui-mvp', 0)} "
            f"class_tui_advanced={classifications.get('tui-advanced', 0)} "
            f"class_browser_fallback={classifications.get('browser-fallback', 0)} "
            f"class_not_terminal={classifications.get('not-suitable-for-terminal', 0)}"
        )
    )
    return "\n".join(lines)


def _parse_json_object(raw: str, *, default: dict[str, Any] | None = None) -> tuple[dict[str, Any], str | None]:
    text = str(raw or "").strip()
    if not text:
        return default or {}, None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return default or {}, f"json_parse_error:{exc.msg}"
    if not isinstance(parsed, dict):
        return default or {}, "json_must_be_object"
    return parsed, None


@dataclass(frozen=True)
class ConfigEditRuntime:
    summary_line: str | None
    config_response: ClientResponse
