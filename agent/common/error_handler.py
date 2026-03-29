import logging
from flask import Flask, request
from werkzeug.exceptions import HTTPException
from agent.common.errors import (
    AnantaError,
    PermanentError,
    TransientError,
    api_response,
)
from agent.common.errors import (
    ValidationError as AnantaValidationError,
)
from agent.common.logging import get_correlation_id

def register_error_handler(app: Flask) -> None:
    @app.errorhandler(Exception)
    def handle_exception(e):
        cid = get_correlation_id()
        if isinstance(e, HTTPException):
            code = getattr(e, "code", 500) or 500
            if code == 404:
                logging.info(f"Erwarteter HTTP-Fehler {code} [CID: {cid}]: {e}")
            elif code < 500:
                logging.warning(f"HTTP-Fehler {code} [CID: {cid}]: {e}")
            else:
                logging.exception(f"HTTP-Serverfehler {code} [CID: {cid}]: {e}")
        elif isinstance(e, AnantaError):
            logging.warning(f"{e.__class__.__name__} [CID: {cid}]: {e}")
        else:
            logging.exception(f"Unbehandelte Exception [CID: {cid}]: {e}")

        if isinstance(e, AnantaValidationError):
            return api_response(
                status="error", message="validation_failed", data={"details": e.details, "cid": cid}, code=422
            )
        if isinstance(e, PermanentError):
            data = {"cid": cid}
            if e.details:
                data["details"] = e.details
            return api_response(status="error", message=str(e), data=data, code=getattr(e, "status_code", 400))
        if isinstance(e, TransientError):
            data = {"cid": cid, "retryable": bool(getattr(e, "retryable", True))}
            if e.details:
                data["details"] = e.details
            return api_response(status="error", message=str(e), data=data, code=getattr(e, "status_code", 503))

        code = getattr(e, "code", 500) if hasattr(e, "code") else 500
        msg = str(e) if code != 500 else "Ein interner Fehler ist aufgetreten."
        return api_response(status="error", message=msg, data={"cid": cid}, code=code)
