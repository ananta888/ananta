from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from client_surfaces.operator_tui.ai_snake_training_store import build_training_bundle, read_patterns


def export_training_bundle_to_path(
    *,
    output_path: str,
    include_events: bool = False,
) -> Path:
    target = Path(output_path).expanduser()
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    bundle = build_training_bundle(include_events=include_events)
    target.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def export_training_markdown(*, output_path: str, json_ref: str = "") -> Path:
    target = Path(output_path).expanduser()
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    patterns = [item for item in read_patterns() if isinstance(item, dict)]
    active = [item for item in patterns if str(item.get("status") or "") == "active"]
    disabled = [item for item in patterns if str(item.get("status") or "") == "disabled"]
    lines = [
        "# AI-Snake Training Report",
        "",
        f"- Aktive Patterns: {len(active)}",
        f"- Deaktivierte Patterns: {len(disabled)}",
        "",
        "## Datenschutz",
        "",
        "Dieser Report enthält keine privaten Roh-Notes.",
    ]
    if json_ref:
        lines.extend(["", f"JSON-Export: `{json_ref}`"])
    lines.extend(["", "## Patterns", ""])
    for item in active + disabled:
        lines.extend(
            [
                f"- `{item.get('pattern_id')}` [{item.get('status')}]",
                f"  - confidence: {float(item.get('confidence') or 0.0):.2f}",
                f"  - human_explanation: {str(item.get('human_explanation') or '')[:240]}",
                f"  - last_seen_at: {str(item.get('last_seen_at') or '-')}",
            ]
        )
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return target


def preview_training_bundle(path: str) -> dict[str, Any]:
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("bundle is not an object")
    return {
        "schema_version": str(payload.get("schema_version") or ""),
        "profile_name": str(((payload.get("profile") or {}).get("display_name")) or "unknown"),
        "patterns": len(payload.get("patterns") or []),
        "privacy_manifest": dict(payload.get("privacy_manifest") or {}),
    }
