from typing import Any, Optional

from flask import Response, jsonify


def api_response(data: Any = None, status: str = "success", message: Optional[str] = None, code: int = 200) -> Response:
    """
    Erzeugt eine standardisierte API-Antwort.
    Format: { "status": "success/error/...", "data": ..., "message": ... }
    """
    response_body = {"status": status}
    if data is not None:
        response_body["data"] = data
    if message is not None:
        response_body["message"] = message

    return jsonify(response_body), code


class AnantaError(Exception):
    """Basis-Exception für das Projekt."""

    def __init__(
        self,
        message: str,
        details: dict | None = None,
        *,
        status_code: int = 500,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.details = details or {}
        self.status_code = int(status_code)
        self.retryable = bool(retryable)


class TransientError(AnantaError):
    """Fehler, der bei einem erneuten Versuch behoben sein könnte (z.B. Timeout, 503)."""

    def __init__(self, message: str, details: dict | None = None, *, status_code: int = 503, retryable: bool = True):
        super().__init__(message, details, status_code=status_code, retryable=retryable)


class PermanentError(AnantaError):
    """Fehler, der nicht durch einfaches Wiederholen behoben wird (z.B. 400, 401, 404)."""

    def __init__(self, message: str, details: dict | None = None, *, status_code: int = 400):
        super().__init__(message, details, status_code=status_code, retryable=False)


class ValidationError(PermanentError):
    """Spezifischer Fehler für Validierungsfehler."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, details, status_code=422)


class TaskNotFoundError(PermanentError):
    def __init__(self, message: str = "not_found", details: dict | None = None):
        super().__init__(message, details, status_code=404)


class TaskConflictError(PermanentError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, details, status_code=409)


class ToolGuardrailError(PermanentError):
    def __init__(self, message: str = "tool_guardrail_blocked", details: dict | None = None):
        super().__init__(message, details, status_code=400)


class WorkerForwardingError(TransientError):
    def __init__(self, message: str = "forwarding_failed", details: dict | None = None):
        super().__init__(message, details, status_code=502, retryable=True)
