from flask import Flask, request
from werkzeug.exceptions import HTTPException
from agent.common.errors import (
    AnantaError,
    PermanentError,
    TransientError,
    api_response,
    normalize_status_code,
)
from agent.common.errors import (
    ValidationError as AnantaValidationError,
)
from agent.common.logging import get_correlation_id
from agent.services.log_service import get_log_service

def register_error_handler(app: Flask) -> None:
    logger = get_log_service().bind(__name__)

    @app.errorhandler(Exception)
    def handle_exception(e):
        cid = get_correlation_id()
        if isinstance(e, HTTPException):
            code = normalize_status_code(getattr(e, "code", 500) or 500, default=500)
            if code == 404:
                logger.info(
                    "Erwarteter HTTP-Fehler %s: %s",
                    code,
                    e,
                    extra_fields={"cid": cid, "http_status": code, "error_type": type(e).__name__},
                )
            elif code < 500:
                logger.warning(
                    "HTTP-Fehler %s: %s",
                    code,
                    e,
                    extra_fields={"cid": cid, "http_status": code, "error_type": type(e).__name__},
                )
            else:
                logger.exception(
                    "HTTP-Serverfehler %s: %s",
                    code,
                    e,
                    extra_fields={"cid": cid, "http_status": code, "error_type": type(e).__name__},
                )
        elif isinstance(e, AnantaError):
            status_code = normalize_status_code(getattr(e, "status_code", 500), default=500)
            log_method = logger.warning if status_code < 500 else logger.error
            log_method(
                "%s: %s",
                e.__class__.__name__,
                e,
                extra_fields={
                    "cid": cid,
                    "error_type": e.__class__.__name__,
                    "status_code": status_code,
                    "retryable": bool(getattr(e, "retryable", False)),
                    "details": getattr(e, "details", None) or {},
                    "path": request.path,
                },
            )
        else:
            logger.exception(
                "Unbehandelte Exception: %s",
                e,
                extra_fields={"cid": cid, "error_type": type(e).__name__, "path": request.path},
            )

        if isinstance(e, AnantaValidationError):
            return api_response(
                status="error",
                message="validation_failed",
                data={"details": e.details, "cid": cid, "error_type": e.__class__.__name__, "retryable": False},
                code=422,
            )
        if isinstance(e, PermanentError):
            data = {"cid": cid, "error_type": e.__class__.__name__, "retryable": False}
            if e.details:
                data["details"] = e.details
            return api_response(status="error", message=str(e), data=data, code=getattr(e, "status_code", 400))
        if isinstance(e, TransientError):
            data = {"cid": cid, "error_type": e.__class__.__name__, "retryable": bool(getattr(e, "retryable", True))}
            if e.details:
                data["details"] = e.details
            return api_response(status="error", message=str(e), data=data, code=getattr(e, "status_code", 503))

        code = normalize_status_code(getattr(e, "code", 500) if hasattr(e, "code") else 500, default=500)
        msg = str(e) if code != 500 else "Ein interner Fehler ist aufgetreten."
        return api_response(status="error", message=msg, data={"cid": cid}, code=code)
