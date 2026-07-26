"""Strict Worker-only ingress for Hub-owned Recovery artifacts."""

from __future__ import annotations

from flask import Blueprint, g, request

from agent.auth import check_registered_worker_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.recovery_artifact_ingress_service import (
    RecoveryArtifactIngressError,
    get_recovery_artifact_ingress_service,
)
from agent.services.workflow_worker_service_auth import (
    RECOVERY_ARTIFACT_INGRESS_SCOPE,
)
from agent.utils import rate_limit

recovery_artifact_ingress_bp = Blueprint(
    "recovery_artifact_ingress",
    __name__,
)
_MAX_MANIFEST_HTTP_BYTES = 262_144


@recovery_artifact_ingress_bp.post(
    "/internal/tasks/<task_id>/recovery-artifacts"
)
@rate_limit(
    limit=120,
    window=60,
    namespace="recovery_artifact_ingress",
)
@check_registered_worker_auth(
    scope=RECOVERY_ARTIFACT_INGRESS_SCOPE
)
def recovery_artifact_ingress(task_id: str):
    content_length = int(request.content_length or 0)
    if (
        content_length <= 0
        or content_length > _MAX_MANIFEST_HTTP_BYTES
    ):
        return api_response(
            status="error",
            message="recovery_artifact_manifest_size_invalid",
            data={
                "reason_code": (
                    "recovery_artifact_manifest_size_invalid"
                )
            },
            code=413,
        )
    identity = dict(getattr(g, "service_identity", {}) or {})
    worker_id = str(identity.get("worker_id") or "")
    worker_url = str(identity.get("worker_url") or "")
    auth_header = str(
        request.headers.get("Authorization") or ""
    )
    worker_token = (
        auth_header.removeprefix("Bearer ").strip()
        if auth_header.startswith("Bearer ")
        else ""
    )
    lease_token = str(
        request.headers.get(
            "X-Ananta-Recovery-Dispatch-Lease"
        )
        or ""
    ).strip()
    manifest = request.get_json(silent=True)
    try:
        receipts = (
            get_recovery_artifact_ingress_service().materialize(
                task_id=task_id,
                manifest=manifest,
                lease_token=lease_token,
                worker_id=worker_id,
                worker_url=worker_url,
                worker_token=worker_token,
            )
        )
    except RecoveryArtifactIngressError as exc:
        log_audit(
            "recovery_artifact_ingress_denied",
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
        "recovery_artifact_ingress_materialized",
        {
            "task_id": str(task_id or ""),
            "worker_id": worker_id,
            "worker_url": worker_url,
            "manifest_digest": receipts["manifest_digest"],
            "artifact_count": len(receipts["artifacts"]),
            "replayed": bool(receipts["replayed"]),
        },
    )
    return api_response(data=receipts)


__all__ = ["recovery_artifact_ingress_bp"]
