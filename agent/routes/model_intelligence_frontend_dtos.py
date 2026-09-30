"""Bounded Angular-facing DTO projections of Model-Intelligence API payloads.

Pure mapping functions without Flask access: the blueprint in
:mod:`agent.routes.model_intelligence` decides when a response is projected
and passes the payload plus configured limits in explicitly (SRP).
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, Mapping

_JOB_STATUS_ALIASES = {
    "submission_pending": "queued",
    "queued": "queued",
    "claimed": "claimed",
    "running": "running",
    "cancel_requested": "cancel_requested",
    "succeeded": "completed",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}
_DEFAULT_PROGRESS = {
    "queued": 0,
    "claimed": 5,
    "running": 50,
    "cancel_requested": 50,
    "completed": 100,
    "failed": 100,
    "cancelled": 100,
    "unknown": 0,
}


def bounded_integer(value: object, *, default: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(0, min(parsed, maximum))


def frontend_capabilities_dto(
    payload: Mapping[str, Any],
    *,
    default_max_graph_nodes: int,
    default_max_graph_edges: int,
) -> dict[str, object]:
    nested = payload.get("capabilities") if isinstance(payload.get("capabilities"), dict) else payload
    limits = nested.get("limits") if isinstance(nested.get("limits"), dict) else {}
    normalized: dict[str, object] = {
        "supported": bool(nested.get("supported", True)),
        "max_graph_nodes": bounded_integer(
            nested.get("max_graph_nodes") or limits.get("max_graph_nodes"),
            default=default_max_graph_nodes,
            maximum=10_000,
        ),
        "max_graph_edges": bounded_integer(
            nested.get("max_graph_edges") or limits.get("max_graph_edges"),
            default=default_max_graph_edges,
            maximum=20_000,
        ),
    }
    if nested.get("reason_code") is not None:
        normalized["reason_code"] = str(nested["reason_code"])
    return normalized


def frontend_job_dto(source: object) -> dict[str, object]:
    value = source if isinstance(source, dict) else {}
    extensions = value.get("extensions") if isinstance(value.get("extensions"), dict) else {}
    identity = (
        value.get("model_identity")
        if isinstance(value.get("model_identity"), dict)
        else {}
    )
    state = str(value.get("status") or value.get("state") or "unknown")
    status = _JOB_STATUS_ALIASES.get(state, "unknown")
    default_progress = _DEFAULT_PROGRESS[status]
    requested = (
        value.get("requested_artifact_kinds")
        or value.get("requested_artifacts")
        or value.get("artifact_kinds")
        or []
    )
    if not isinstance(requested, (list, tuple)):
        requested = []
    job_id = str(value.get("job_id") or "unknown")
    model_id = (
        value.get("model_id")
        or identity.get("model_id")
        or identity.get("identity_id")
        or extensions.get("import_ref")
        or job_id
    )
    error = value.get("error") if isinstance(value.get("error"), dict) else {}
    reason_code = value.get("reason_code") or error.get("reason_code")
    result: dict[str, object] = {
        "job_id": job_id,
        "model_id": str(model_id),
        "analysis_kind": str(value.get("analysis_kind") or "full"),
        "profile_id": str(value.get("profile_id") or extensions.get("profile_id") or "bounded-ui"),
        "requested_artifact_kinds": [str(item) for item in requested],
        "status": status,
        "progress_percent": bounded_integer(
            value.get("progress_percent"),
            default=default_progress,
            maximum=100,
        ),
    }
    optional_values = {
        "schema": value.get("schema"),
        "hub_task_id": value.get("hub_task_id"),
        "import_ref": value.get("import_ref") or extensions.get("import_ref"),
        "request_sha256": value.get("request_sha256") or value.get("request_digest"),
        "max_runtime_seconds": value.get("max_runtime_seconds"),
        "max_output_bytes": value.get("max_output_bytes"),
        "reason_code": reason_code,
        "created_at": value.get("created_at"),
        "updated_at": value.get("updated_at"),
    }
    result.update({key: item for key, item in optional_values.items() if item is not None})
    return result


def frontend_report_dto(source: object) -> dict[str, object]:
    value = source if isinstance(source, dict) else {}
    nested = value.get("report") if isinstance(value.get("report"), dict) else value
    raw_sections = nested.get("sections") if isinstance(nested, dict) else []
    if not isinstance(raw_sections, list):
        raw_sections = []
    sections: list[dict[str, object]] = []
    for index, item in enumerate(raw_sections):
        section = item if isinstance(item, dict) else {"data": item}
        raw_status = str(section.get("status") or "available")
        status = raw_status if raw_status in {"available", "unsupported", "not_run", "failed"} else "failed"
        normalized: dict[str, object] = {
            "name": str(section.get("name") or section.get("section") or f"section-{index + 1}"),
            "status": status,
            "data": section.get("data"),
        }
        if section.get("reason_code") is not None:
            normalized["reason_code"] = str(section["reason_code"])
        sections.append(normalized)
    schema = str(nested.get("schema") or "ananta.model-intelligence.report.v1")
    digest = nested.get("content_digest") or nested.get("sha256")
    if not isinstance(digest, str) or not digest:
        canonical = json.dumps(
            {"schema": schema, "sections": sections},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        digest = f"sha256:{sha256(canonical).hexdigest()}"
    return {"schema": schema, "content_digest": digest, "sections": sections}


def frontend_graph_dto(source: object) -> dict[str, object]:
    value = source if isinstance(source, dict) else {}
    nested = value.get("graph") if isinstance(value.get("graph"), dict) else value
    raw_nodes = nested.get("nodes") if isinstance(nested, dict) else []
    raw_edges = nested.get("edges") if isinstance(nested, dict) else []
    nodes: list[dict[str, str]] = []
    edges: list[dict[str, str]] = []
    if isinstance(raw_nodes, list):
        for item in raw_nodes:
            node = item if isinstance(item, dict) else {}
            node_id = str(node.get("node_id") or node.get("id") or "")
            if node_id:
                nodes.append(
                    {
                        "node_id": node_id,
                        "label": str(node.get("label") or node.get("name") or node_id),
                        "kind": str(node.get("kind") or node.get("type") or "unknown"),
                    }
                )
    if isinstance(raw_edges, list):
        for item in raw_edges:
            edge = item if isinstance(item, dict) else {}
            source_id = str(edge.get("source_node_id") or edge.get("source") or "")
            target_id = str(edge.get("target_node_id") or edge.get("target") or "")
            if source_id and target_id:
                edge_id = str(
                    edge.get("edge_id")
                    or edge.get("id")
                    or f"{source_id}:{target_id}:{len(edges)}"
                )
                edges.append(
                    {
                        "edge_id": edge_id,
                        "source_node_id": source_id,
                        "target_node_id": target_id,
                        "kind": str(edge.get("kind") or edge.get("type") or "unknown"),
                    }
                )
    return {
        "schema": str(nested.get("schema") or "ananta.model-intelligence.graph.v1"),
        "nodes": nodes,
        "edges": edges,
        "truncated": bool(nested.get("truncated", False)),
    }


__all__ = [
    "bounded_integer",
    "frontend_capabilities_dto",
    "frontend_graph_dto",
    "frontend_job_dto",
    "frontend_report_dto",
]
