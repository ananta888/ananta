from __future__ import annotations

from typing import Any


FILTER_KEYS = ("source_id", "artifact_type", "sensitivity", "status", "worker_id")


def normalize_goal_artifact_filters(filters: dict[str, Any] | None) -> dict[str, str]:
    payload = dict(filters or {})
    normalized: dict[str, str] = {}
    for key in FILTER_KEYS:
        value = str(payload.get(key) or "").strip()
        if value:
            normalized[key] = value
    return normalized


def apply_goal_artifact_filters(rows: list[dict[str, Any]], filters: dict[str, Any] | None) -> list[dict[str, Any]]:
    active = normalize_goal_artifact_filters(filters)
    if not active:
        return list(rows)
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _matches_row(row, active):
            filtered.append(row)
    return filtered


def filter_goal_artifact_view(
    *,
    source_grants: list[dict[str, Any]],
    source_usages: list[dict[str, Any]],
    output_artifacts: list[dict[str, Any]],
    filters: dict[str, Any] | None,
) -> dict[str, list[dict[str, Any]]]:
    return {
        "source_grants": apply_goal_artifact_filters(source_grants, filters),
        "source_usages": apply_goal_artifact_filters(source_usages, filters),
        "output_artifacts": apply_goal_artifact_filters(output_artifacts, filters),
    }


def _matches_row(row: dict[str, Any], filters: dict[str, str]) -> bool:
    for key, expected in filters.items():
        if key == "source_id":
            value = _row_source_id(row)
        else:
            value = str(row.get(key) or "")
        if value.strip().lower() != expected.strip().lower():
            return False
    return True


def _row_source_id(row: dict[str, Any]) -> str:
    source_id = str(row.get("source_id") or "").strip()
    if source_id:
        return source_id
    artifact_ref = str(row.get("artifact_ref") or "").strip()
    if artifact_ref.startswith("sources:"):
        parts = artifact_ref.split(":")
        if len(parts) >= 2:
            return parts[1]
    return ""
