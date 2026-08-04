"""Request-auth policy for Hub-issued knowledge-index Worker dispatches."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

KNOWLEDGE_INDEX_DISPATCH_SERVICE_AUTH_REQUIRED = (
    "knowledge_index_dispatch_service_auth_required"
)

_HUB_SERVICE_AUTH_MODES = frozenset(
    {
        "agent_jwt",
        "agent_static_token",
    }
)


@dataclass(frozen=True)
class KnowledgeIndexDispatchRequestAuthDecision:
    """One request-local decision without depending on Flask globals."""

    dispatch_present: bool
    allowed: bool
    reason_code: str | None = None


class KnowledgeIndexDispatchRequestAuthPolicy:
    """Permit internal dispatch markers only from the existing Hub bearer."""

    @staticmethod
    def evaluate(
        request_data: Any,
        *,
        user_identity: Mapping[str, Any] | None,
        service_identity: Mapping[str, Any] | None,
    ) -> KnowledgeIndexDispatchRequestAuthDecision:
        marker = getattr(request_data, "knowledge_index_dispatch", None)
        if marker is None and isinstance(request_data, Mapping):
            marker = request_data.get("knowledge_index_dispatch")
        if marker is None:
            return KnowledgeIndexDispatchRequestAuthDecision(
                dispatch_present=False,
                allowed=True,
            )

        user = dict(user_identity or {})
        service = dict(service_identity or {})
        auth_mode = str(service.get("auth_mode") or "").strip().lower()
        allowed = not user and auth_mode in _HUB_SERVICE_AUTH_MODES
        return KnowledgeIndexDispatchRequestAuthDecision(
            dispatch_present=True,
            allowed=allowed,
            reason_code=(
                None
                if allowed
                else KNOWLEDGE_INDEX_DISPATCH_SERVICE_AUTH_REQUIRED
            ),
        )


knowledge_index_dispatch_request_auth_policy = (
    KnowledgeIndexDispatchRequestAuthPolicy()
)


__all__ = [
    "KNOWLEDGE_INDEX_DISPATCH_SERVICE_AUTH_REQUIRED",
    "KnowledgeIndexDispatchRequestAuthDecision",
    "KnowledgeIndexDispatchRequestAuthPolicy",
    "knowledge_index_dispatch_request_auth_policy",
]
