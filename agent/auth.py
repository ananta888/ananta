import logging
import os
import secrets
import stat
import time
from functools import wraps
from pathlib import Path
from typing import Any, Mapping

import jwt
from flask import current_app, g, request

from agent.common.audit import log_audit
from agent.common.errors import PermanentError, api_response
from agent.config import settings
from agent.utils import register_with_hub

INVALID_TOKEN_WARN_LAST: dict[tuple[str, str], float] = {}
_AGENT_TOKEN_FILE_MIN_BYTES = 32
_AGENT_TOKEN_FILE_MAX_BYTES = 16_384


class AgentTokenConfigurationError(RuntimeError):
    """Raised when a configured file-managed service token is unsafe."""


def _agent_token_file_reference(config: Mapping[str, Any] | None = None) -> str:
    source = config if config is not None else current_app.config
    configured = source.get("AGENT_TOKEN_FILE")
    return str(configured or os.environ.get("AGENT_TOKEN_FILE") or "").strip()


def resolve_configured_agent_token(
    config: Mapping[str, Any] | None = None,
) -> str | None:
    """Resolve the agent token, preferring a bounded read-only file secret.

    The file is intentionally read for every authentication attempt so an
    atomic secret-file replacement becomes effective without caching secret
    material in process-global state.
    """

    source = config if config is not None else current_app.config
    inline_token = str(source.get("AGENT_TOKEN") or "")
    raw_path = _agent_token_file_reference(source)
    if not raw_path:
        return inline_token or None

    path = Path(raw_path)
    if not path.is_absolute():
        raise AgentTokenConfigurationError("agent token file reference must be absolute")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise AgentTokenConfigurationError("agent token file cannot be inspected") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise AgentTokenConfigurationError("agent token file must be a regular file")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise AgentTokenConfigurationError("agent token file permissions are unsafe")

    try:
        with path.open("rb") as handle:
            raw_token = handle.read(_AGENT_TOKEN_FILE_MAX_BYTES + 1)
    except OSError as exc:
        raise AgentTokenConfigurationError("agent token file cannot be read") from exc
    if not raw_token or len(raw_token) > _AGENT_TOKEN_FILE_MAX_BYTES:
        raise AgentTokenConfigurationError("agent token file size is invalid")
    try:
        token = raw_token.decode("utf-8").strip()
    except UnicodeError as exc:
        raise AgentTokenConfigurationError("agent token file encoding is invalid") from exc
    token_bytes = token.encode("utf-8")
    if (
        len(token_bytes) < _AGENT_TOKEN_FILE_MIN_BYTES
        or len(token_bytes) > _AGENT_TOKEN_FILE_MAX_BYTES
        or "\x00" in token
        or any(character.isspace() for character in token)
    ):
        raise AgentTokenConfigurationError("agent token file value is invalid")
    if inline_token and not secrets.compare_digest(inline_token.encode("utf-8"), token_bytes):
        raise AgentTokenConfigurationError("inline and file-managed agent tokens conflict")
    return token


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
    if _agent_token_file_reference():
        # File-managed service credentials must never enter URLs, proxy logs,
        # browser history or referrer headers. Query-token support remains a
        # legacy-only compatibility path for inline deployments.
        return None
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
        logging.warning(
            "User-JWT secret_key is shorter than 32 bytes; "
            "JWT validation remains enabled but is weakly configured."
        )


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


def _authenticate_request(
    provided_token: str | None,
    *,
    require_admin: bool = False,
    allow_auth_disabled: bool = True,
) -> tuple[bool, str | None]:
    try:
        agent_token = resolve_configured_agent_token()
    except AgentTokenConfigurationError as exc:
        logging.error("File-managed agent token configuration rejected: %s", exc)
        return False, "agent_token_file_invalid"
    if not agent_token and not require_admin:
        # Auth-disabled legacy deployments still need to preserve the identity
        # of a valid Hub user JWT. Otherwise exposure-policy evaluation sees an
        # unknown caller even though the user completed a real Hub login.
        if provided_token:
            try:
                user_payload = _validate_user_jwt(provided_token)
            except jwt.ExpiredSignatureError:
                return False, "expired_token"
            if user_payload:
                _set_user_auth_context(user_payload)
                return True, "user_jwt"
        if allow_auth_disabled:
            logging.warning("Agent läuft OHNE Authentifizierung! Setzen Sie AGENT_TOKEN für mehr Sicherheit.")
            _set_agent_admin_context({"auth_mode": "auth_disabled"})
            return True, "auth_disabled"
        return False, "invalid_token"

    if not provided_token:
        return False, "missing_token"

    if agent_token:
        if provided_token.count(".") == 2:
            payload = _validate_agent_jwt(provided_token, agent_token)
            if payload:
                _set_agent_admin_context(payload)
                return True, "agent_jwt"
        elif secrets.compare_digest(provided_token.encode("utf-8"), agent_token.encode("utf-8")):
            _set_agent_admin_context()
            return True, "agent_static_token"
    elif require_admin:
        logging.warning(
            "Admin route requested without AGENT_TOKEN configured; only user JWT admin auth remains available."
        )

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


def authenticate_provided_token(
    provided_token: str | None,
    *,
    require_admin: bool = False,
) -> tuple[bool, str | None]:
    """Authenticate a token in an active Flask context without reading HTTP request fields.

    WebSocket facades use this boundary before accepting protocol messages. It
    intentionally applies the same JWT/static-token rules and populates the
    same request-local identity as the HTTP decorators.
    """

    return _authenticate_request(provided_token, require_admin=require_admin)


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


def _strict_bearer_error(*, service_only: bool):
    """Authenticate a strict bearer and optionally require service identity."""

    auth_header = str(request.headers.get("Authorization") or "")
    if not auth_header.startswith("Bearer "):
        return api_response(
            status="error",
            message="unauthorized",
            data={"reason_code": "workflow_auth_bearer_required"},
            code=401,
        )
    provided_token = auth_header.removeprefix("Bearer ").strip()
    if not provided_token:
        return api_response(
            status="error",
            message="unauthorized",
            data={"reason_code": "workflow_auth_bearer_required"},
            code=401,
        )

    authenticated, auth_mode = _authenticate_request(
        provided_token,
        require_admin=False,
        allow_auth_disabled=False,
    )
    if not authenticated:
        _warn_auth_failure(auth_mode or "invalid_token")
        if auth_mode == "agent_token_file_invalid":
            return api_response(
                status="error",
                message="service unavailable",
                data={"reason_code": "workflow_auth_configuration_invalid"},
                code=503,
            )
        return api_response(
            status="error",
            message="unauthorized",
            data={"reason_code": "workflow_auth_invalid"},
            code=401,
        )
    if service_only and auth_mode not in {"agent_jwt", "agent_static_token"}:
        log_audit(
            "workflow_service_auth_denied",
            {"path": request.path, "method": request.method, "auth_mode": auth_mode},
        )
        return api_response(
            status="error",
            message="forbidden",
            data={"reason_code": "workflow_service_auth_required"},
            code=403,
        )
    return None


def check_strict_auth(f):
    """Require a real user or service bearer even in auth-disabled setups."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        error = _strict_bearer_error(service_only=False)
        if error is not None:
            return error
        return f(*args, **kwargs)

    return wrapper


def check_service_auth(f):
    """Require an agent credential; browser/user JWTs are always rejected."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        error = _strict_bearer_error(service_only=True)
        if error is not None:
            return error
        return f(*args, **kwargs)

    return wrapper


def check_user_auth(f):
    """Validate a Hub-issued user JWT.

    Keycloak/OIDC tokens belong to the independent Pair/WebRTC identity
    sphere.  They may be exchanged for a Hub session by the explicit,
    opt-in account-link flow, but are never accepted here directly.
    """

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
    if _agent_token_file_reference():
        raise PermanentError(
            "Token-Rotation wird extern verwaltet, solange AGENT_TOKEN_FILE konfiguriert ist."
        )
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


def get_request_auth_context() -> dict:
    """Returns normalized auth context for policy services."""
    user_payload = dict(getattr(g, "user", {}) or {})
    auth_payload = dict(getattr(g, "auth_payload", {}) or {})
    if user_payload:
        return user_payload
    if auth_payload:
        return auth_payload
    return {}
