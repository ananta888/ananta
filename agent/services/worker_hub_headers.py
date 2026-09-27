"""Request headers a registered worker sends to its Hub: agent token plus workflow service identity.

Shared by every worker -> Hub client (CodeCompass layer jobs, graph artifacts) instead of each building
them itself.
"""

from __future__ import annotations

from agent.config import settings


def registered_worker_hub_headers() -> dict[str, str]:
    """Bearer token and identity headers; raises ``ValueError`` for an incomplete worker identity."""
    from agent.auth import resolve_configured_agent_token
    from worker.runtime.workflow_service_identity import WorkflowServiceIdentity

    token = resolve_configured_agent_token(
        {"AGENT_TOKEN": settings.agent_token, "AGENT_TOKEN_FILE": settings.agent_token_file}
    )
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    identity = WorkflowServiceIdentity.optional(worker_id=settings.agent_name, worker_url=str(settings.agent_url or ""))
    if identity is not None:
        headers.update(identity.headers())
    return headers
