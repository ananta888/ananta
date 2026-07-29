"""Least-privilege authentication for Vector-Index dispatch redemption."""

from __future__ import annotations

import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ananta_contracts.vector_index_dispatch import (
    canonicalize_vector_index_worker_audience,
)

_MIN_TOKEN_BYTES = 32
_MAX_TOKEN_BYTES = 16_384


class VectorIndexWorkerIdentityError(RuntimeError):
    """Stable, bounded authentication failure for the internal endpoint."""

    def __init__(
        self,
        reason_code: str,
        *,
        status_code: int = 401,
    ) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True, slots=True)
class VectorIndexWorkerIdentity:
    worker_id: str
    worker_url: str


def authenticate_vector_index_worker(
    *,
    provided_token: str,
    claimed_worker_id: str,
    claimed_worker_url: str,
    registered_agents: Iterable[Any],
    forbidden_tokens: Iterable[str] = (),
    config: Mapping[str, Any] | None = None,
) -> VectorIndexWorkerIdentity:
    """Bind one bearer to one active, capable, registered Worker."""

    token = str(provided_token or "").strip()
    encoded = token.encode("utf-8")
    worker_id = str(claimed_worker_id or "").strip()
    try:
        worker_url = canonicalize_vector_index_worker_audience(
            claimed_worker_url
        )
    except ValueError as exc:
        raise VectorIndexWorkerIdentityError(
            "vector_index_worker_identity_invalid"
        ) from exc
    if (
        not worker_id
        or len(worker_id.encode("utf-8")) > 256
        or not _MIN_TOKEN_BYTES
        <= len(encoded)
        <= _MAX_TOKEN_BYTES
    ):
        raise VectorIndexWorkerIdentityError(
            "vector_index_worker_identity_invalid"
        )
    for forbidden in forbidden_tokens:
        candidate = str(forbidden or "")
        if candidate and secrets.compare_digest(token, candidate):
            raise VectorIndexWorkerIdentityError(
                "vector_index_worker_credential_reuse_denied",
                status_code=403,
            )

    agents = tuple(registered_agents)
    forbidden = tuple(
        str(value or "") for value in forbidden_tokens
    )
    from agent.services.workflow_worker_service_auth import (
        VECTOR_INDEX_DISPATCH_ADMISSION_SCOPE,
        WorkflowWorkerAuthConfigurationError,
        WorkflowWorkerAuthDenied,
        authenticate_registered_workflow_worker,
    )

    try:
        identity = authenticate_registered_workflow_worker(
            token,
            required_scope=(
                VECTOR_INDEX_DISPATCH_ADMISSION_SCOPE
            ),
            claimed_worker_id=worker_id,
            claimed_worker_url=worker_url,
            registered_agents=agents,
            hub_service_token=(
                forbidden[0] if forbidden else None
            ),
            user_session_secret=(
                forbidden[1] if len(forbidden) > 1 else None
            ),
            config=config,
        )
    except WorkflowWorkerAuthConfigurationError as exc:
        raise VectorIndexWorkerIdentityError(
            "vector_index_worker_identity_configuration_invalid",
            status_code=503,
        ) from exc
    except WorkflowWorkerAuthDenied as exc:
        raise VectorIndexWorkerIdentityError(
            (
                "vector_index_worker_identity_forbidden"
                if exc.status_code == 403
                else "vector_index_worker_identity_invalid"
            ),
            status_code=exc.status_code,
        ) from exc

    worker = next(
        (
            value
            for value in agents
            if str(getattr(value, "name", "") or "").strip()
            == identity.worker_id
        ),
        None,
    )
    advertised_capabilities = {
        str(value).strip().lower()
        for value in (
            getattr(worker, "capabilities", None)
            or ()
        )
        if str(value).strip()
    }
    required_capabilities = {
        "retrieval",
        "index_write",
        "vector_index_operation",
    }
    if (
        worker is None
        or not required_capabilities.issubset(
            advertised_capabilities
        )
        or not required_capabilities.issubset(
            set(identity.capabilities)
        )
    ):
        raise VectorIndexWorkerIdentityError(
            "vector_index_worker_identity_forbidden",
            status_code=403,
        )
    return VectorIndexWorkerIdentity(
        worker_id=identity.worker_id,
        worker_url=canonicalize_vector_index_worker_audience(
            identity.worker_url
        ),
    )


__all__ = [
    "VectorIndexWorkerIdentity",
    "VectorIndexWorkerIdentityError",
    "authenticate_vector_index_worker",
]
