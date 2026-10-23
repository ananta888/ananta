from __future__ import annotations

from typing import Final

from flask import Flask

from agent.config import settings
from agent.services.ananta_tool_registry_service import get_ananta_tool_registry_service

CORE_TOOLS: Final = (
    "codecompass.plan_context",
    "codecompass.search",
    "codecompass.search_symbols",
    "codecompass.get_file_context",
)


def initialize_codecompass_worker_tools(app: Flask) -> None:
    if settings.role != "worker":
        app.extensions["codecompass_worker_tools_status"] = {
            "registered": False,
            "reason": "worker_role_required",
        }
        return
    registry = get_ananta_tool_registry_service()
    missing = [name for name in CORE_TOOLS if registry.get_tool(name) is None]
    if missing:
        app.extensions["codecompass_worker_tools_status"] = {
            "registered": False,
            "reason": "codecompass_core_tools_missing",
            "missing_tools": missing,
        }
        return
    registration = dict(app.extensions.get("workflow_adapter_worker_registration") or {})
    registration["capabilities"] = sorted({
        *(registration.get("capabilities") or []),
        "codecompass",
    })
    app.extensions["workflow_adapter_worker_registration"] = registration
    app.extensions["codecompass_worker_tools_status"] = {
        "registered": True,
        "tools": list(CORE_TOOLS),
        "task_authorization_required": True,
    }
