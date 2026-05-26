from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_SOURCE_SCHEMA_FILE = Path(__file__).resolve().parents[3] / "schemas" / "tui" / "diff_source_ref.v1.json"
_PANEL_SCHEMA_FILE = Path(__file__).resolve().parents[3] / "schemas" / "tui" / "diff_panel_config.v1.json"


def _load_schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(payload: dict[str, Any], *, schema_path: Path) -> list[str]:
    validator = Draft202012Validator(_load_schema(schema_path))
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
    return [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]


def validate_diff_source_ref(payload: dict[str, Any]) -> list[str]:
    return _validate(payload, schema_path=_SOURCE_SCHEMA_FILE)


def validate_diff_panel_config(payload: dict[str, Any]) -> list[str]:
    return _validate(payload, schema_path=_PANEL_SCHEMA_FILE)


def build_diff_source_ref(
    *,
    source_ref_id: str,
    source_kind: str,
    display_name: str,
    locator: dict[str, Any],
    content_hash: str | None = None,
    provenance_ref: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "diff_source_ref.v1",
        "source_ref_id": str(source_ref_id),
        "source_kind": str(source_kind),
        "display_name": str(display_name),
        "locator": dict(locator or {}),
    }
    if content_hash:
        payload["content_hash"] = str(content_hash)
    if provenance_ref:
        payload["provenance_ref"] = str(provenance_ref)
    errors = validate_diff_source_ref(payload)
    if errors:
        raise ValueError(f"invalid_diff_source_ref:{'; '.join(errors)}")
    return payload


def build_diff_panel_config(
    *,
    panel_id: str,
    render_mode: str,
    filters: dict[str, Any] | None = None,
    title_template: str = "",
    compact_title: str = "",
    follow_active_file: bool | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "diff_panel_config.v1",
        "panel_id": str(panel_id),
        "render_mode": str(render_mode),
        "filters": dict(filters or {}),
    }
    if title_template:
        payload["title_template"] = str(title_template)
    if compact_title:
        payload["compact_title"] = str(compact_title)
    if follow_active_file is not None:
        payload["follow_active_file"] = bool(follow_active_file)
    errors = validate_diff_panel_config(payload)
    if errors:
        raise ValueError(f"invalid_diff_panel_config:{'; '.join(errors)}")
    return payload


def build_current_diff_source_ref(*, source_ref_id: str = "current-diff", path_filter: str = "") -> dict[str, Any]:
    locator: dict[str, Any] = {"base_ref": "HEAD", "target": "working_tree"}
    if path_filter.strip():
        locator["path_filter"] = path_filter.strip()
    return build_diff_source_ref(
        source_ref_id=source_ref_id,
        source_kind="git_diff",
        display_name="Current Diff",
        locator=locator,
    )


def build_output_artifact_source_ref(*, output_artifact_id: str, goal_id: str | None = None) -> dict[str, Any]:
    locator: dict[str, Any] = {"output_artifact_id": str(output_artifact_id)}
    if str(goal_id or "").strip():
        locator["goal_id"] = str(goal_id).strip()
    return build_diff_source_ref(
        source_ref_id=f"output-{output_artifact_id}",
        source_kind="goal_output_artifact",
        display_name=f"Output {output_artifact_id}",
        locator=locator,
    )
