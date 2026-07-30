from __future__ import annotations

import time
from typing import Any

from flask import Flask, g, request

from agent.common.logging import get_correlation_id
from agent.services.execution_audit_service import get_execution_audit_service
from agent.services.http_audit_policy import get_http_audit_policy
from agent.services.http_request_metrics import get_http_request_metrics


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
        decision = get_http_audit_policy().request_started(
            method=request.method,
            path=request.path,
        )
        if not decision.emit_start:
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
        duration_ms = int((time.time() - started) * 1000)
        decision = get_http_audit_policy().request_completed(
            method=request.method,
            path=request.path,
            status_code=int(response.status_code),
        )
        if decision.emit_completion:
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
                    "duration_ms": duration_ms,
                },
            )
        elif decision.observe_metrics:
            get_http_request_metrics().observe(
                method=request.method,
                status_code=int(response.status_code),
                duration_ms=duration_ms,
            )
        return response
