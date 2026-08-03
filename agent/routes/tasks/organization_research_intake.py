"""Service-only Hub-to-Worker intake for Organization planning research."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

from flask import Blueprint, request

from agent.auth import check_service_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.config import settings
from agent.services.organization_research_worker_intake_service import (
    OrganizationResearchWorkerIntakeError,
    get_organization_research_worker_intake_service,
)

organization_research_intake_bp = Blueprint(
    "organization_research_intake",
    __name__,
)


def _configured_worker_url() -> str:
    worker_url = str(settings.agent_url or "").strip().rstrip("/")
    try:
        parsed = urlsplit(worker_url)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not hostname
        or any(character.isspace() for character in worker_url)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return ""
    return worker_url


@organization_research_intake_bp.post(
    "/internal/tasks/organization-planning-research"
)
@check_service_auth
def admit_organization_planning_research():
    if str(settings.role or "").strip().lower() != "worker":
        return api_response(
            status="error",
            message="organization_research_worker_role_required",
            data={
                "reason_code": "organization_research_worker_role_required"
            },
            code=403,
        )
    raw = request.get_json(silent=True)
    if not isinstance(raw, Mapping):
        return api_response(
            status="error",
            message="organization_research_dispatch_payload_invalid",
            data={
                "reason_code": (
                    "organization_research_dispatch_payload_invalid"
                )
            },
            code=400,
        )
    worker_url = _configured_worker_url()
    if not worker_url:
        return api_response(
            status="error",
            message=(
                "organization_research_worker_identity_unavailable"
            ),
            data={
                "reason_code": (
                    "organization_research_worker_identity_unavailable"
                )
            },
            code=503,
        )
    try:
        result = get_organization_research_worker_intake_service().admit(
            raw,
            worker_url=worker_url,
        )
    except OrganizationResearchWorkerIntakeError as exc:
        log_audit(
            "organization_research_dispatch_denied",
            {
                "task_id": str(raw.get("id") or ""),
                "parent_task_id": str(raw.get("parent_task_id") or ""),
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
        "organization_research_dispatch_admitted",
        {
            "task_id": str(result.get("task_id") or ""),
            "context_bundle_id": str(
                result.get("context_bundle_id") or ""
            ),
            "replayed": bool(result.get("replayed")),
        },
    )
    return api_response(
        data=result,
        code=200 if result.get("replayed") else 202,
    )


__all__ = ["organization_research_intake_bp"]
