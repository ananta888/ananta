from __future__ import annotations

import time
from typing import Any

from flask import Flask, g, request

from agent.common.logging import get_correlation_id
from agent.services.execution_audit_service import get_execution_audit_service


def _request_target() -> dict[str, Any]:
    return {
        "http_method": request.method,
        "path": request.path,
        "endpoint": request.endpoint,
    }


def register_audit_middleware(app: Flask) -> None:
    @app.before_request
    def _audit_before_request() -> None:
        g._audit_started_at = time.time()
        if not request.path.startswith(("/api", "/v1/mcp")):
            return
        get_execution_audit_service().emit(
            operation_type="http_request_started",
            outcome="accepted",
            trace_id=get_correlation_id(),
            goal_id=None,
            task_id=request.headers.get("X-Task-ID"),
            actor="http_client",
            actor_role="api",
            policy_version="kritis-audit-v1",
            target=_request_target(),
            details={"query": dict(request.args)},
        )

    @app.after_request
    def _audit_after_request(response):
        started = float(getattr(g, "_audit_started_at", time.time()))
        if request.path.startswith(("/api", "/v1/mcp")):
            get_execution_audit_service().emit(
                operation_type="http_request_completed",
                outcome="success" if int(response.status_code) < 400 else "error",
                trace_id=get_correlation_id(),
                goal_id=None,
                task_id=request.headers.get("X-Task-ID"),
                actor="http_client",
                actor_role="api",
                policy_version="kritis-audit-v1",
                target=_request_target(),
                details={
                    "status_code": int(response.status_code),
                    "duration_ms": int((time.time() - started) * 1000),
                },
            )
        return response
