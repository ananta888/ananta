from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

_ALLOWED_MODES = {"review", "explain", "risk", "tests", "patch", "chat"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def validate_ai_diff_panel_state(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if str(payload.get("schema") or "") != "ai_diff_panel_state.v1":
        errors.append("schema: must be ai_diff_panel_state.v1")
    mode = str(payload.get("mode") or "")
    if mode not in _ALLOWED_MODES:
        errors.append("mode: unsupported")
    selected_panels = list(payload.get("selected_panels") or [])
    if not selected_panels:
        errors.append("selected_panels: at least one panel required")
    for panel in selected_panels:
        if str(panel) not in {"A", "B", "C"}:
            errors.append("selected_panels: panel must be A/B/C")
            break
    if not isinstance(payload.get("selected_hunks"), list):
        errors.append("selected_hunks: must be list")
    if not isinstance(payload.get("context_refs"), list):
        errors.append("context_refs: must be list")
    if not str(payload.get("prompt_template_ref") or "").strip():
        errors.append("prompt_template_ref: required")
    if not str(payload.get("status") or "").strip():
        errors.append("status: required")
    return errors


def build_ai_diff_panel_state(
    *,
    mode: str,
    selected_panels: list[str] | None = None,
    selected_hunks: list[str] | None = None,
    context_refs: list[str] | None = None,
    prompt_template_ref: str | None = None,
    status: str = "idle",
    last_response_ref: str = "",
) -> dict[str, Any]:
    selected = [str(item) for item in (selected_panels or ["A", "B"]) if str(item) in {"A", "B", "C"}]
    payload = {
        "schema": "ai_diff_panel_state.v1",
        "mode": str(mode),
        "selected_panels": selected or ["A"],
        "selected_hunks": [str(item) for item in (selected_hunks or []) if str(item).strip()],
        "context_refs": [str(item) for item in (context_refs or []) if str(item).strip()],
        "prompt_template_ref": str(prompt_template_ref or f"prompt:diff3/{mode}"),
        "status": str(status),
        "last_response_ref": str(last_response_ref),
        "updated_at": _now_iso(),
    }
    errors = validate_ai_diff_panel_state(payload)
    if errors:
        raise ValueError(f"invalid_ai_diff_panel_state:{'; '.join(errors)}")
    return payload


def set_ai_diff_mode(payload: dict[str, Any], *, mode: str, status: str = "idle") -> dict[str, Any]:
    next_payload = dict(payload)
    next_payload["mode"] = str(mode)
    next_payload["prompt_template_ref"] = f"prompt:diff3/{mode}"
    next_payload["status"] = str(status)
    next_payload["updated_at"] = _now_iso()
    errors = validate_ai_diff_panel_state(next_payload)
    if errors:
        raise ValueError(f"invalid_ai_diff_panel_state:{'; '.join(errors)}")
    return next_payload

