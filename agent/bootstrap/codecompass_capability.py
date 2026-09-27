"""Register the Hub's CodeCompass capability issuer (WCRB-007).

``resolve_request_capability`` reads the resolver from ``app.extensions``; it
was never registered, so every in-Hub CodeCompass call (MCP, companion) ran
without a capability and retrieval failed closed. Only the Hub issues
capabilities: workers receive signed ones with their task.
"""

from __future__ import annotations

from flask import Flask

from agent.config import settings


def initialize_codecompass_capability(app: Flask) -> None:
    if settings.role == "worker":
        app.extensions["codecompass_capability_issuer_status"] = {"registered": False, "reason": "worker_role"}
        return
    from agent.services.codecompass_capability_issuer import HubRetrievalCapabilityResolver

    app.extensions["codecompass_retrieval_capability_resolver"] = HubRetrievalCapabilityResolver()
    app.extensions["codecompass_capability_issuer_status"] = {"registered": True, "reason": None}
