import logging
import os
import uuid

from flask import Flask, request

from agent.common.errors import api_response
from agent.common.logging import JsonFormatter, set_correlation_id
from agent.config import settings


def configure_audit_logger() -> None:
    audit_file = os.path.join(settings.data_dir, "audit.log")
    audit_handler = logging.FileHandler(audit_file, encoding="utf-8")
    if settings.log_json:
        audit_handler.setFormatter(JsonFormatter())
    else:
        audit_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))

    audit_logger = logging.getLogger("audit")
    audit_logger.setLevel(logging.INFO)
    audit_logger.addHandler(audit_handler)
    audit_logger.propagate = False


def register_request_hooks(app: Flask) -> None:
    @app.before_request
    def ensure_correlation_id_and_check_shutdown():
        import agent.common.context

        cid = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        set_correlation_id(cid)
        if agent.common.context.shutdown_requested and request.endpoint not in (
            "system.health",
            "tasks.get_logs",
            "tasks.task_logs",
        ):
            return api_response(status="shutdown_in_progress", code=503)

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        default_csp = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "report-uri /api/system/csp-report;"
        )
        swagger_csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "report-uri /api/system/csp-report;"
        )
        swagger_paths = ("/apidocs", "/apispec", "/flasgger_static")
        csp = swagger_csp if request.path.startswith(swagger_paths) else default_csp
        response.headers.setdefault("Content-Security-Policy", csp)
        is_https = request.is_secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https"
        if is_https:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains; preload")
        return response

