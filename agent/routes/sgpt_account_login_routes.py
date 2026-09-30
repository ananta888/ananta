"""Worker-local, browser-assisted CLI account-login endpoint of the ``sgpt`` blueprint.

Registered on ``sgpt_bp`` by :mod:`agent.routes.sgpt` (which owns the URL and the
admin decorator); this module only maps the bounded login actions onto the
account-login service and audits the outcome (SRP).
"""

from __future__ import annotations

from flask import request

from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.config import settings


def cli_backend_account_login(backend_id: str):
    """Manage a browser-assisted account login inside one Worker."""

    from agent.cli_backends.account_login import (
        SUPPORTED_ACCOUNT_LOGIN_BACKENDS,
        CliBackendAccountLoginError,
        get_cli_backend_account_login_service,
    )

    backend = str(backend_id or "").strip().lower()
    if backend not in SUPPORTED_ACCOUNT_LOGIN_BACKENDS:
        return api_response(status="error", message="account_login_backend_unsupported", code=404)
    if settings.role != "worker":
        return api_response(status="error", message="worker_role_required", code=409)

    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "").strip().lower()
    service = get_cli_backend_account_login_service()
    try:
        if action == "account_status":
            result = service.account_status(backend)
        elif action == "login_start":
            result = service.start(backend)
        elif action == "login_status":
            result = service.status(backend, str(body.get("session_id") or ""))
        elif action == "login_input":
            result = service.submit_input(
                backend,
                str(body.get("session_id") or ""),
                str(body.get("value") or ""),
            )
        elif action == "login_cancel":
            result = service.cancel(backend, str(body.get("session_id") or ""))
        else:
            return api_response(status="error", message="invalid_account_login_action", code=400)
    except CliBackendAccountLoginError as exc:
        reason_code = str(exc)
        response_code = 404 if reason_code in {"backend_not_installed", "account_login_session_not_found"} else 400
        log_audit(
            "cli_backend_account_login_failed",
            {"backend": backend, "action": action, "reason_code": reason_code},
        )
        return api_response(
            status="error",
            message=reason_code,
            data={"backend": backend, "action": action},
            code=response_code,
        )

    log_audit(
        "cli_backend_account_login_action",
        {
            "backend": backend,
            "action": action,
            "login_status": result.get("status"),
        },
    )
    return api_response(data=result)


__all__ = ["cli_backend_account_login"]
