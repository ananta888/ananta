from typing import Any, Optional, TypeVar

from flask import Response, g, jsonify

from agent.common.redaction import VisibilityLevel, redact


def _restore_auth_response_tokens(original: Any, redacted: Any) -> Any:
    if not isinstance(original, dict) or not isinstance(redacted, dict):
        return redacted
    token_keys = {"access_token", "refresh_token"}
    if not token_keys.intersection(original):
        return redacted
    restored = dict(redacted)
    for key in token_keys:
        if key in original:
            restored[key] = original[key]
    return restored


def normalize_status_code(code: Any, *, default: int = 500) -> int:
    try:
        normalized = int(code)
    except (TypeError, ValueError):
        return default
    if 100 <= normalized <= 599:
        return normalized
    return default


def user_error_guidance(*, code: int | str, message: str | None = None) -> dict[str, Any]:
    """Return user-facing recovery help for common API error classes."""
    code_value = normalize_status_code(code, default=500)
    text = str(message or "").lower()
    if code_value in {401, 403}:
        return {
            "summary": "Zugriff nicht moeglich.",
            "next_steps": [
                "Melde dich erneut an oder pruefe den verwendeten Token.",
                "Pruefe, ob dein Benutzer die noetige Rolle oder Freigabe hat.",
            ],
        }
    if code_value == 404:
        return {
            "summary": "Die angefragte Ressource wurde nicht gefunden.",
            "next_steps": [
                "Pruefe die ID oder aktualisiere die Ansicht.",
                "Lege das Ziel erneut an, falls es noch nicht existiert.",
            ],
        }
    if code_value in {409, 412, 422} or "policy" in text or "governance" in text or "blocked" in text:
        return {
            "summary": "Die Anfrage wurde durch Validierung oder Governance gestoppt.",
            "next_steps": [
                "Formuliere das Ziel enger und beschreibe klare Grenzen.",
                "Pruefe Governance-Modus, Review-Pflicht oder fehlende Bereitschaftssignale.",
            ],
        }
    if code_value >= 500:
        return {
            "summary": "Der Hub konnte die Anfrage gerade nicht verarbeiten.",
            "next_steps": [
                "Pruefe Hub-Logs und Systemstatus.",
                "Wiederhole die Aktion, sobald der Hub wieder gesund ist.",
            ],
        }
    return {
        "summary": "Die Anfrage konnte nicht verarbeitet werden.",
        "next_steps": [
            "Pruefe Eingaben und Pflichtfelder.",
            "Starte mit einem kleineren Ziel oder pruefe den Systemstatus.",
        ],
    }


def _with_error_guidance(data: Any, *, status: str, message: str | None, code: int) -> Any:
    if status != "error":
        return data
    guidance = user_error_guidance(code=code, message=message)
    if isinstance(data, dict):
        enriched = dict(data)
        enriched.setdefault("error_help", guidance)
        return enriched
    return {"error_help": guidance}


def api_response(data: Any = None, status: str = "success", message: Optional[str] = None, code: int | str = 200) -> Response:
    """
    Erzeugt eine standardisierte API-Antwort.
    Format: { "status": "success/error/...", "data": ..., "message": ... }
    """
    normalized_code = normalize_status_code(code, default=500 if status == "error" else 200)
    response_body = {"status": status}
    response_data = _with_error_guidance(data, status=status, message=message, code=normalized_code)
    if response_data is not None:
        # Automatisches Redacting basierend auf dem aktuellen Kontext
        visibility = VisibilityLevel.USER
        try:
            if hasattr(g, "is_admin") and g.is_admin:
                visibility = VisibilityLevel.ADMIN
            elif not hasattr(g, "user") or not g.user:
                # Kein User-Kontext -> Public (strengste Maskierung)
                visibility = VisibilityLevel.PUBLIC
        except (RuntimeError, AttributeError):
            # Außerhalb eines Request-Kontexts
            pass

        response_body["data"] = _restore_auth_response_tokens(response_data, redact(response_data, visibility=visibility))
    if message is not None:
        response_body["message"] = message

    return jsonify(response_body), normalized_code


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


class BadRequestError(PermanentError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, details, status_code=400)


class NotFoundError(PermanentError):
    def __init__(self, message: str = "not_found", details: dict | None = None):
        super().__init__(message, details, status_code=404)


class ConflictError(PermanentError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, details, status_code=409)


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


class ConfigError(PermanentError):
    def __init__(self, message: str = "config_error", details: dict | None = None):
        super().__init__(message, details, status_code=500)


class PlanningError(TransientError):
    def __init__(self, message: str = "planning_failed", details: dict | None = None):
        super().__init__(message, details, status_code=502, retryable=True)


class VerificationError(PermanentError):
    def __init__(self, message: str = "verification_failed", details: dict | None = None):
        super().__init__(message, details, status_code=422)


class RateLimitError(TransientError):
    def __init__(self, message: str = "rate_limit_exceeded", details: dict | None = None):
        super().__init__(message, details, status_code=429, retryable=True)


AnantaErrorT = TypeVar("AnantaErrorT", bound=AnantaError)


def merge_error_details(*parts: dict | None, **extra: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for part in parts:
        if isinstance(part, dict):
            merged.update({str(key): value for key, value in part.items() if value is not None})
    merged.update({str(key): value for key, value in extra.items() if value is not None})
    return merged


def with_error_context(error: AnantaErrorT, **details: Any) -> AnantaErrorT:
    error.details = merge_error_details(getattr(error, "details", None), details)
    return error
