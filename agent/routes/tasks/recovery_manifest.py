"""Internal, assignment-bound Recovery child manifest endpoint."""

from __future__ import annotations

from flask import Blueprint, g, jsonify

from agent.auth import check_registered_worker_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.recovery_task_manifest_service import (
    RecoveryTaskManifestDenied,
    get_recovery_task_manifest_service,
)
from agent.services.workflow_worker_service_auth import (
    RECOVERY_TASK_MANIFEST_SCOPE,
)

recovery_manifest_bp = Blueprint(
    "recovery_task_manifest",
    __name__,
)


@recovery_manifest_bp.get(
    "/internal/tasks/<task_id>/recovery-child-manifest"
)
@check_registered_worker_auth(
    scope=RECOVERY_TASK_MANIFEST_SCOPE
)
def recovery_child_manifest(task_id: str):
    identity = dict(getattr(g, "service_identity", {}) or {})
    worker_id = str(identity.get("worker_id") or "")
    worker_url = str(identity.get("worker_url") or "")
    try:
        manifest = (
            get_recovery_task_manifest_service().manifest_for_worker(
                task_id=task_id,
                worker_url=worker_url,
            )
        )
    except RecoveryTaskManifestDenied as exc:
        log_audit(
            "recovery_task_manifest_denied",
            {
                "task_id": str(task_id or ""),
                "worker_id": worker_id,
                "worker_url": worker_url,
                "reason_code": exc.reason_code,
            },
        )
        return api_response(
            status="error",
            message=exc.reason_code,
            data={"reason_code": exc.reason_code},
            code=exc.status_code,
        )

    log_audit(
        "recovery_task_manifest_read",
        {
            "task_id": str(task_id or ""),
            "worker_id": worker_id,
            "worker_url": worker_url,
            "schema": manifest["schema"],
        },
    )
    # The exact approval-bound payload must not be transformed by the generic
    # public/user response redactor.  Authentication and assignment checks
    # above are the dedicated visibility boundary for this internal response.
    response = jsonify({"status": "success", "data": manifest})
    response.headers["Cache-Control"] = "no-store"
    return response


__all__ = ["recovery_manifest_bp"]
