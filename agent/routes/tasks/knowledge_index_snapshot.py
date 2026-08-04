"""Internal Worker-authenticated knowledge-index base-task snapshot."""

from __future__ import annotations

import json

from flask import Blueprint, current_app, g

from agent.auth import check_registered_worker_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.knowledge_index_task_snapshot_service import (
    KnowledgeIndexTaskSnapshotDenied,
    get_knowledge_index_task_snapshot_service,
)
from agent.services.workflow_worker_service_auth import (
    KNOWLEDGE_INDEX_TASK_SNAPSHOT_SCOPE,
)

knowledge_index_snapshot_bp = Blueprint(
    "knowledge_index_task_snapshot",
    __name__,
)


@knowledge_index_snapshot_bp.get("/internal/tasks/<task_id>/knowledge-index-base-snapshot")
@check_registered_worker_auth(scope=KNOWLEDGE_INDEX_TASK_SNAPSHOT_SCOPE)
def knowledge_index_base_snapshot(task_id: str):
    identity = dict(getattr(g, "service_identity", {}) or {})
    worker_id = str(identity.get("worker_id") or "")
    worker_url = str(identity.get("worker_url") or "")
    try:
        snapshot = get_knowledge_index_task_snapshot_service().snapshot_for_worker(
            task_id=task_id,
            worker_id=worker_id,
            worker_url=worker_url,
        )
    except KnowledgeIndexTaskSnapshotDenied as exc:
        log_audit(
            "knowledge_index_task_snapshot_denied",
            {
                "task_id": str(task_id or ""),
                "worker_id": worker_id,
                "reason_code": exc.reason_code,
            },
        )
        public_reason_code = (
            "not_found" if exc.status_code == 404 else exc.reason_code
        )
        return api_response(
            status="error",
            message=public_reason_code,
            data={"reason_code": public_reason_code},
            code=exc.status_code,
        )

    log_audit(
        "knowledge_index_task_snapshot_read",
        {
            "task_id": str(task_id or ""),
            "worker_id": worker_id,
            "schema": snapshot["schema"],
        },
    )
    response = current_app.response_class(
        json.dumps(
            {"status": "success", "data": snapshot},
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ),
        status=200,
        mimetype="application/json",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


__all__ = ["knowledge_index_snapshot_bp"]
