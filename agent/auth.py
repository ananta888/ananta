import logging
import secrets
import time
from functools import wraps

import jwt
from flask import current_app, g, request

from agent.common.errors import PermanentError, api_response
from agent.config import settings
from agent.utils import register_with_hub


def generate_token(payload: dict, secret: str, expires_in: int | None = None):
    """Generiert einen JWT-Token."""
    if expires_in is None:
        expires_in = settings.auth_access_token_ttl_seconds
    payload["exp"] = time.time() + expires_in
    return jwt.encode(payload, secret, algorithm="HS256")


def _extract_token_from_request() -> str | None:
    """Extrahiert Token aus Authorization-Header oder Query-Parameter."""
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.split(" ")[1]
    return request.args.get("token")


def _validate_agent_jwt(token: str, agent_token: str) -> dict | None:
    """Validiert einen JWT gegen den AGENT_TOKEN.

    Returns payload if valid, None if token too short or invalid.
    """
    if not agent_token or len(agent_token.encode("utf-8")) < 32:
        return None
    try:
        return jwt.decode(token, agent_token, algorithms=["HS256"], leeway=30)
    except jwt.PyJWTError:
        return None


def _validate_user_jwt(token: str) -> dict | None:
    """Validiert einen User-JWT gegen settings.secret_key.

    Returns payload if valid, None if invalid.
    Raises jwt.ExpiredSignatureError for expired tokens.
    """
    try:
        return jwt.decode(token, settings.secret_key, algorithms=["HS256"], leeway=30)
    except jwt.ExpiredSignatureError:
        raise
    except jwt.PyJWTError:
        return None


def _set_admin_context(payload: dict | None, user_payload: dict | None = None):
    """Setzt Admin-Kontext basierend auf Validierungsergebnis."""
    if payload:
        g.auth_payload = payload
        g.is_admin = True
    elif user_payload:
        g.user = user_payload
        g.is_admin = user_payload.get("role") == "admin"


def check_auth(f):
    """Decorator zur Prüfung der JWT-Authentifizierung."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        token = current_app.config.get("AGENT_TOKEN")
        if not token:
            logging.warning("Agent läuft OHNE Authentifizierung! Setzen Sie AGENT_TOKEN für mehr Sicherheit.")
            return f(*args, **kwargs)

        provided_token = _extract_token_from_request()
        if not provided_token:
            return api_response(
                status="error",
                message="unauthorized",
                data={"details": "Missing Authorization (header or token param)"},
                code=401,
            )

        try:
            if provided_token.count(".") == 2:
                payload = _validate_agent_jwt(provided_token, token)
                if payload:
                    _set_admin_context(payload)
                else:
                    user_payload = _validate_user_jwt(provided_token)
                    if user_payload:
                        _set_admin_context(None, user_payload)
                    else:
                        raise jwt.PyJWTError("Invalid JWT")
            else:
                if provided_token != token:
                    raise Exception("Invalid static token")
                g.is_admin = True
        except Exception as e:
            logging.warning(f"Authentifizierungsfehler von {request.remote_addr}: {e}")
            return api_response(status="error", message="unauthorized", data={"details": "Invalid token"}, code=401)

        return f(*args, **kwargs)

    return wrapper


def check_user_auth(f):
    """Prüft auf einen gültigen Benutzer-JWT."""

    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return api_response(status="error", message="User authentication required", code=401)

        token = auth_header.split(" ")[1]
        try:
            payload = _validate_user_jwt(token)
            if payload is None:
                return api_response(status="error", message="Invalid token", code=401)
            g.user = payload
            g.is_admin = payload.get("role") == "admin"
        except jwt.ExpiredSignatureError:
            return api_response(status="error", message="Token expired", code=401)

        return f(*args, **kwargs)

    return decorated


def _try_agent_token_auth(provided_token: str | None, agent_token: str | None) -> bool:
    """Versucht Authentifizierung via AGENT_TOKEN (JWT oder static).

    Returns True if successful and sets g.is_admin.
    """
    if not provided_token or not agent_token:
        return False

    if provided_token.count(".") == 2:
        payload = _validate_agent_jwt(provided_token, agent_token)
        if payload:
            g.is_admin = True
            return True
    elif provided_token == agent_token:
        g.is_admin = True
        return True
    return False


def _try_user_auth(provided_token: str | None) -> bool:
    """Versucht Authentifizierung via User-JWT.

    Returns True if user is admin.
    """
    if not provided_token:
        return False

    payload = _validate_user_jwt(provided_token)
    if payload and payload.get("role") == "admin":
        g.user = payload
        g.is_admin = True
        return True
    return False


def admin_required(f):
    """Erfordert Admin-Rechte (entweder via AGENT_TOKEN oder via User-Role)."""

    @wraps(f)
    def decorated(*args, **kwargs):
        if not hasattr(g, "is_admin"):
            agent_token = current_app.config.get("AGENT_TOKEN")
            provided_token = _extract_token_from_request()

            if not _try_agent_token_auth(provided_token, agent_token):
                _try_user_auth(provided_token)

        if not getattr(g, "is_admin", False):
            return api_response(
                status="error", message="forbidden", data={"details": "Admin privileges required"}, code=403
            )

        return f(*args, **kwargs)

    return decorated


def rotate_token():
    """Generiert einen neuen Secret-Token und aktualisiert die Config sowie die Persistenz."""
    new_secret = secrets.token_urlsafe(32)

    # Synchronisation mit dem Hub versuchen, BEVOR wir den Token lokal festschreiben
    hub_url = settings.hub_url
    agent_name = current_app.config.get("AGENT_NAME")
    if hub_url and agent_name:
        success = register_with_hub(
            hub_url=hub_url,
            agent_name=agent_name,
            port=settings.port,
            token=new_secret,
            role=current_app.config.get("ROLE", "worker"),
        )
        if not success:
            logging.error("Token-Rotation abgebrochen: Registrierung am Hub fehlgeschlagen.")
            raise PermanentError("Token-Rotation fehlgeschlagen: Synchronisation mit Hub nicht möglich.")

    current_app.config["AGENT_TOKEN"] = new_secret

    # Persistieren
    try:
        settings.save_agent_token(new_secret)
    except Exception as e:
        # Hier loggen wir nur, da der Hub den Token bereits hat.
        # Ein Rollback wäre jetzt noch komplizierter.
        logging.error(f"Fehler beim Persistieren des Tokens: {e}")

    logging.info("Agent Secret/Token wurde rotiert.")
    return new_secret
