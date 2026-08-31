"""Stable response projections for Control-Center API resources."""

from __future__ import annotations

from typing import Any

from agent.db_models import PolicySnapshotDB, ToolCallDB

_ALLOWED_AGENT_SESSION_STATUSES = {
    "idle",
    "proposed",
    "running",
    "waiting_for_approval",
    "blocked",
    "review",
    "verified",
    "done",
    "failed",
    "cancelled",
}


def normalize_agent_session_status(raw: str | None) -> str:
    status = str(raw or "").strip().lower()
    if status in _ALLOWED_AGENT_SESSION_STATUSES:
        return status
    if status == "canceled":
        return "cancelled"
    return "idle"


def tool_call_item(row: ToolCallDB) -> dict[str, Any]:
    return {
        "id": str(row.id or ""),
        "session_id": str(row.session_id or ""),
        "task_id": str(row.task_id or "") or None,
        "action_id": str(row.action_id or ""),
        "tool_name": str(row.tool_name or ""),
        "status": str(row.status or ""),
        "risk_level": str(row.risk_level or "medium"),
        "target_path": str(row.target_path or "") or None,
        "created_at": float(row.created_at or 0.0),
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "error_message": row.error_message,
    }


def policy_snapshot_item(snapshot: PolicySnapshotDB) -> dict[str, Any]:
    return {
        "id": str(snapshot.id or ""),
        "session_id": str(snapshot.session_id or ""),
        "task_id": str(snapshot.task_id or "") or None,
        "policy_version": str(snapshot.policy_version or "v1"),
        "risk_level": str(snapshot.risk_level or "medium"),
        "allowed_tools": list(snapshot.allowed_tools_json or []),
        "denied_tools": list(snapshot.denied_tools_json or []),
        "allowed_paths": list(snapshot.allowed_paths_json or []),
        "denied_paths": list(snapshot.denied_paths_json or []),
        "cloud_allowed": bool(snapshot.cloud_allowed),
        "runtime_boundary": str(snapshot.runtime_boundary or "unknown"),
        "requires_human_approval": bool(snapshot.requires_human_approval),
        "approval_reason": snapshot.approval_reason,
        "created_at": float(snapshot.created_at or 0.0),
    }


def artifact_item(artifact: Any) -> dict[str, Any]:
    metadata = dict(getattr(artifact, "artifact_metadata", None) or {})
    safe_metadata_fields = (
        "content_hash",
        "project_id",
        "session_id",
        "task_id",
        "type",
    )
    return {
        "id": str(getattr(artifact, "id", "") or ""),
        "latest_media_type": str(getattr(artifact, "latest_media_type", "") or "") or None,
        "latest_filename": str(getattr(artifact, "latest_filename", "") or "") or None,
        "artifact_metadata": {
            key: value
            for key in safe_metadata_fields
            if key in metadata
            and (
                (value := metadata.get(key)) is None
                or isinstance(value, (str, int, float, bool))
            )
        },
        "created_at": float(getattr(artifact, "created_at", 0.0) or 0.0),
        "updated_at": float(getattr(artifact, "updated_at", 0.0) or 0.0),
    }
