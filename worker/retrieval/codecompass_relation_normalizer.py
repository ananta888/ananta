from __future__ import annotations

from typing import Any

_SUPPORTED_RELATIONS = {
    "child_of_file",
    "child_of_type",
    "declares_method",
    "calls_probable_target",
    "injects_dependency",
    "field_type_uses",
    "extends",
    "implements",
    "jpa_relation",
    "transactional_boundary",
    "declares_bean",
}


def _normalize_relation_type(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return "related"
    if normalized in _SUPPORTED_RELATIONS:
        return normalized
    return normalized.replace(" ", "_")


def normalize_relation_records(*, records: list[dict[str, Any]], manifest_hash: str) -> dict[str, Any]:
    resolved_edges: list[dict[str, Any]] = []
    unresolved_candidates: list[dict[str, Any]] = []
    malformed_count = 0
    for index, record in enumerate(list(records or []), start=1):
        if not isinstance(record, dict):
            malformed_count += 1
            continue
        relation_type = _normalize_relation_type(
            str(record.get("relation") or record.get("type") or record.get("relation_type") or "")
        )
        source_id = str(
            record.get("source_id")
            or record.get("from_id")
            or record.get("from")
            or record.get("record_id")
            or ""
        ).strip()
        if not source_id:
            malformed_count += 1
            continue
        confidence = float(record.get("confidence") or (0.8 if relation_type in _SUPPORTED_RELATIONS else 0.6))
        target_id = str(record.get("target_id") or record.get("to_id") or record.get("target") or record.get("to") or "").strip()
        provenance = {
            "manifest_hash": str(manifest_hash or ""),
            "record_id": str(record.get("id") or f"relation:{index}"),
        }
        if target_id:
            resolved_edges.append(
                {
                    "edge_type": relation_type,
                    "source_id": source_id,
                    "target_id": target_id,
                    "confidence": confidence,
                    "provenance": provenance,
                }
            )
            continue
        unresolved_target = str(
            record.get("target_text")
            or record.get("target_symbol")
            or record.get("target_name")
            or record.get("target_type")
            or ""
        ).strip()
        unresolved_candidates.append(
            {
                "edge_type": relation_type,
                "source_id": source_id,
                "resolved_target": unresolved_target or None,
                "confidence": min(confidence, 0.35),
                "provenance": provenance,
            }
        )
    return {
        "resolved_edges": resolved_edges,
        "unresolved_candidates": unresolved_candidates,
        "malformed_count": malformed_count,
    }
