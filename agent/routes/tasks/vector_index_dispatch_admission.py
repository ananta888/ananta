"""Hub admission endpoint for delegated Vector index executions."""

from __future__ import annotations

from flask import Blueprint, current_app, request

from agent.auth import resolve_configured_agent_token
from agent.common.errors import api_response
from agent.services.repository_registry import get_repository_registry
from agent.services.vector_index_worker_identity_service import (
    VectorIndexWorkerIdentityError,
    authenticate_vector_index_worker,
)
from agent.utils import rate_limit


def _bearer_token() -> str:
    auth_header = str(
        request.headers.get("Authorization") or ""
    ).strip()
    return (
        auth_header.removeprefix("Bearer ").strip()
        if auth_header.startswith("Bearer ")
        else ""
    )


def _authenticate_worker():
    return authenticate_vector_index_worker(
        provided_token=_bearer_token(),
        claimed_worker_id=str(
            request.headers.get("X-Ananta-Worker-ID") or ""
        ).strip(),
        claimed_worker_url=str(
            request.headers.get("X-Ananta-Worker-URL") or ""
        ).strip(),
        registered_agents=(
            get_repository_registry().agent_repo.get_all() or ()
        ),
        forbidden_tokens=(
            str(
                resolve_configured_agent_token(
                    current_app.config
                )
                or ""
            ),
            str(current_app.secret_key or ""),
        ),
        config=current_app.config,
    )


@rate_limit(
    limit=240,
    window=60,
    namespace="vector_index_dispatch_admission",
)
def vector_index_dispatch_admission(tid: str):
    """Redeem one current execute grant at the Hub control plane."""

    try:
        identity = _authenticate_worker()
    except VectorIndexWorkerIdentityError as exc:
        return api_response(
            status="error",
            message="vector index Worker identity denied",
            data={"reason_code": exc.reason_code},
            code=exc.status_code,
        )

    payload = request.get_json(silent=True)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"attempt_id", "sequence", "phase"}
    ):
        return api_response(
            status="error",
            message="vector index dispatch admission invalid",
            data={
                "reason_code": (
                    "vector_index_dispatch_admission_request_invalid"
                )
            },
            code=400,
        )
    # Resolve at the service boundary so the established test/injection seam
    # on ``vector_index_task_service`` remains effective.
    from agent.services.vector_index_task_service import (
        get_vector_index_task_service,
    )

    try:
        admission = (
            get_vector_index_task_service()
            .admit_dispatch_attempt(
                job_id=str(tid or ""),
                attempt_id=payload.get("attempt_id"),
                sequence=payload.get("sequence"),
                phase=payload.get("phase"),
                worker_audience=identity.worker_url,
                actor=(
                    "vector-index-worker:"
                    + identity.worker_id
                ),
            )
        )
    except ValueError as exc:
        return api_response(
            status="error",
            message="vector index dispatch admission invalid",
            data={"reason_code": str(exc)},
            code=400,
        )
    except RuntimeError as exc:
        return api_response(
            status="error",
            message="vector index dispatch admission denied",
            data={"reason_code": str(exc)},
            code=409,
        )
    return api_response(
        data={
            "allowed": True,
            "reason_code": (
                "vector_index_dispatch_admission_granted"
            ),
            "job_id": str(tid or ""),
            "attempt_id": admission["attempt_id"],
            "sequence": admission["sequence"],
            "phase": admission["phase"],
            "worker_audience": admission["audience"],
        }
    )


def register_vector_index_dispatch_admission_route(
    blueprint: Blueprint,
) -> None:
    blueprint.add_url_rule(
        (
            "/internal/tasks/<tid>/"
            "vector-index-dispatch-admission"
        ),
        endpoint="vector_index_dispatch_admission",
        view_func=vector_index_dispatch_admission,
        methods=["POST"],
    )


__all__ = ["register_vector_index_dispatch_admission_route"]
