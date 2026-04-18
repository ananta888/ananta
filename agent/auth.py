import logging
import secrets
import time
from functools import wraps

import jwt
from flask import current_app, g, request

from agent.common.audit import log_audit
from agent.common.errors import PermanentError, api_response
from agent.config import settings
from agent.utils import register_with_hub

INVALID_TOKEN_WARN_LAST: dict[tuple[str, str], float] = {}


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


def _warn_if_user_jwt_secret_is_weak() -> None:
    secret = str(settings.secret_key or "")
    if len(secret.encode("utf-8")) < 32:
        logging.warning("User-JWT secret_key is shorter than 32 bytes; JWT validation remains enabled but is weakly configured.")


def _validate_user_jwt(token: str) -> dict | None:
    """Validiert einen User-JWT gegen settings.secret_key.

    Returns payload if valid, None if invalid.
    Raises jwt.ExpiredSignatureError for expired tokens.
    """
    _warn_if_user_jwt_secret_is_weak()
    try:
        return jwt.decode(token, settings.secret_key, algorithms=["HS256"], leeway=30)
    except jwt.ExpiredSignatureError:
        raise
    except jwt.PyJWTError:
        return None


def _set_agent_admin_context(payload: dict | None = None) -> None:
    g.auth_payload = payload or {}
    g.user = {}
    g.is_admin = True


def _set_user_auth_context(user_payload: dict) -> None:
    g.user = user_payload
    g.auth_payload = {}
    g.is_admin = user_payload.get("role") == "admin"


def _warn_auth_failure(reason: str) -> None:
    remote = request.remote_addr or "unknown"
    key = (remote, reason)
    now = time.time()
    last_ts = INVALID_TOKEN_WARN_LAST.get(key, 0.0)
    if now - last_ts > 30:
        logging.warning(f"Authentifizierungsfehler von {remote}: {reason}")
        INVALID_TOKEN_WARN_LAST[key] = now
    else:
        logging.debug(f"Authentifizierungsfehler (gedrosselt) von {remote}: {reason}")


def _authenticate_request(provided_token: str | None, *, require_admin: bool = False) -> tuple[bool, str | None]:
    agent_token = current_app.config.get("AGENT_TOKEN")
    if not agent_token and not require_admin:
        logging.warning("Agent läuft OHNE Authentifizierung! Setzen Sie AGENT_TOKEN für mehr Sicherheit.")
        return True, "auth_disabled"

    if not provided_token:
        return False, "missing_token"

    if agent_token:
        if provided_token.count(".") == 2:
            payload = _validate_agent_jwt(provided_token, agent_token)
            if payload:
                _set_agent_admin_context(payload)
                return True, "agent_jwt"
        elif provided_token == agent_token:
            _set_agent_admin_context()
            return True, "agent_static_token"
    elif require_admin:
        logging.warning("Admin route requested without AGENT_TOKEN configured; only user JWT admin auth remains available.")

    try:
        user_payload = _validate_user_jwt(provided_token)
    except jwt.ExpiredSignatureError:
        return False, "expired_token"

    if user_payload:
        _set_user_auth_context(user_payload)
        if require_admin and not getattr(g, "is_admin", False):
            return False, "admin_privileges_required"
        return True, "user_jwt"

    return False, "invalid_token"


def check_auth(f):
    """Decorator zur Prüfung der JWT-Authentifizierung."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        provided_token = _extract_token_from_request()
        authenticated, auth_mode = _authenticate_request(provided_token, require_admin=False)
        if not authenticated:
            if auth_mode == "missing_token":
                return api_response(
                    status="error",
                    message="unauthorized",
                    data={"details": "Missing Authorization (header or token param)"},
                    code=401,
                )
            if auth_mode == "expired_token":
                _warn_auth_failure(auth_mode)
                return api_response(status="error", message="unauthorized", data={"details": "Token expired"}, code=401)

            _warn_auth_failure(auth_mode or "auth_error")
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
            _set_user_auth_context(payload)
        except jwt.ExpiredSignatureError:
            return api_response(status="error", message="Token expired", code=401)

        return f(*args, **kwargs)

    return decorated


def admin_required(f):
    """Erfordert Admin-Rechte (entweder via AGENT_TOKEN oder via User-Role)."""

    @wraps(f)
    def decorated(*args, **kwargs):
        if not hasattr(g, "is_admin"):
            provided_token = _extract_token_from_request()
            authenticated, auth_mode = _authenticate_request(provided_token, require_admin=True)
            if not authenticated:
                if auth_mode in {"missing_token", "expired_token", "invalid_token"}:
                    _warn_auth_failure(auth_mode)
                elif auth_mode == "admin_privileges_required":
                    return api_response(
                        status="error", message="forbidden", data={"details": "Admin privileges required"}, code=403
                    )

        if not getattr(g, "is_admin", False):
            return api_response(
                status="error", message="forbidden", data={"details": "Admin privileges required"}, code=403
            )

        if getattr(g, "auth_payload", None):
            auth_source = "agent_token"
        elif getattr(g, "user", None):
            auth_source = "user_jwt"
        else:
            auth_source = "pre_authenticated_context"
        log_audit("admin_route_accessed", {"path": request.path, "method": request.method, "auth_source": auth_source})

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


def hash_password(password: str) -> str:
    """Einfacher Hash für Tests/Entwicklung (SHA-256)."""
    import hashlib
    return hashlib.sha256(password.encode()).hexdigest()
