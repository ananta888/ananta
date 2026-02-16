from flask import jsonify, Response
from typing import Any, Optional


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

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


class TransientError(AnantaError):
    """Fehler, der bei einem erneuten Versuch behoben sein könnte (z.B. Timeout, 503)."""

    pass


class PermanentError(AnantaError):
    """Fehler, der nicht durch einfaches Wiederholen behoben wird (z.B. 400, 401, 404)."""

    pass


class ValidationError(PermanentError):
    """Spezifischer Fehler für Validierungsfehler."""

    pass
