import logging
import os
import secrets
import stat
import time
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Mapping

import jwt
from flask import current_app, g, request

from agent.common.audit import log_audit
from agent.common.errors import PermanentError, api_response
from agent.config import settings
from agent.services.user_token_scope import (
    control_center_stream_identity_is_bound,
    is_control_center_stream_token,
    token_scope_allows_request,
)
from agent.utils import register_with_hub
from ananta_contracts.file_credentials import (
    FileCredentialConfigurationError,
    read_file_managed_token,
)

INVALID_TOKEN_WARN_LAST: dict[tuple[str, str], float] = {}
_AGENT_TOKEN_FILE_MIN_BYTES = 32
_AGENT_TOKEN_FILE_MAX_BYTES = 16_384


class AgentTokenConfigurationError(RuntimeError):
    """Raised when a configured file-managed service token is unsafe."""


def _validate_agent_token_file_metadata(metadata: os.stat_result) -> None:
    """Validate security properties from an already-open file descriptor."""

    if not stat.S_ISREG(metadata.st_mode):
        raise AgentTokenConfigurationError("agent token file must be a regular file")
    if int(metadata.st_nlink) != 1:
        raise AgentTokenConfigurationError("agent token file link count is unsafe")

    effective_uid = getattr(os, "geteuid", None)
    if not callable(effective_uid):
        raise AgentTokenConfigurationError("agent token file owner cannot be verified")
    if metadata.st_uid not in {0, effective_uid()}:
        raise AgentTokenConfigurationError("agent token file owner is unsafe")

    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise AgentTokenConfigurationError("agent token file permissions are unsafe")
    if metadata.st_size < 1 or metadata.st_size > _AGENT_TOKEN_FILE_MAX_BYTES:
        raise AgentTokenConfigurationError("agent token file size is invalid")


def _agent_token_file_metadata_fingerprint(metadata: os.stat_result) -> tuple[int, ...]:
    """Return the mutation-sensitive metadata that must remain stable while reading."""

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_agent_token_descriptor(file_descriptor: int) -> bytes:
    """Read at most one byte beyond the configured secret-size boundary."""

    chunks: list[bytes] = []
    bytes_read = 0
    read_limit = _AGENT_TOKEN_FILE_MAX_BYTES + 1
    while bytes_read < read_limit:
        chunk = os.read(file_descriptor, min(8192, read_limit - bytes_read))
        if not chunk:
            break
        chunks.append(chunk)
        bytes_read += len(chunk)
    return b"".join(chunks)


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

    no_follow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(no_follow, int) or no_follow == 0:
        raise AgentTokenConfigurationError("agent token file secure open is unsupported")

    open_flags = os.O_RDONLY | no_follow
    open_flags |= int(getattr(os, "O_CLOEXEC", 0))
    open_flags |= int(getattr(os, "O_NONBLOCK", 0))
    try:
        file_descriptor = os.open(path, open_flags)
    except (OSError, ValueError) as exc:
        raise AgentTokenConfigurationError("agent token file cannot be opened securely") from exc

    try:
        try:
            metadata_before = os.fstat(file_descriptor)
            _validate_agent_token_file_metadata(metadata_before)
            raw_token = _read_agent_token_descriptor(file_descriptor)
            metadata_after = os.fstat(file_descriptor)
        except AgentTokenConfigurationError:
            raise
        except OSError as exc:
            raise AgentTokenConfigurationError("agent token file cannot be read securely") from exc
        if _agent_token_file_metadata_fingerprint(metadata_before) != _agent_token_file_metadata_fingerprint(
            metadata_after
        ):
            raise AgentTokenConfigurationError("agent token file changed while being read")
    finally:
        try:
            os.close(file_descriptor)
        except OSError:
            pass

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


def resolve_configured_registration_token(
    config: Mapping[str, Any] | None = None,
) -> str | None:
    """Resolve the bootstrap-only registration credential.

    ``REGISTRATION_TOKEN_FILE`` is deliberately independent from
    ``AGENT_TOKEN_FILE`` so a Worker service bearer cannot mint or overwrite
    another Worker identity. Inline ``REGISTRATION_TOKEN`` remains available
    for non-strict legacy deployments.
    """

    source = config if config is not None else current_app.config
    inline = str(
        source.get("REGISTRATION_TOKEN")
        or getattr(settings, "registration_token", None)
        or ""
    )
    raw_path = str(
        source.get("REGISTRATION_TOKEN_FILE")
        or os.environ.get("REGISTRATION_TOKEN_FILE")
        or ""
    ).strip()
    if not raw_path:
        return inline or None
    try:
        token = read_file_managed_token(
            raw_path,
            description="registration token file",
            min_bytes=_AGENT_TOKEN_FILE_MIN_BYTES,
            max_bytes=_AGENT_TOKEN_FILE_MAX_BYTES,
        )
    except FileCredentialConfigurationError as exc:
        raise AgentTokenConfigurationError(str(exc)) from exc
    if inline and not secrets.compare_digest(inline, token):
        raise AgentTokenConfigurationError(
            "inline and file-managed registration tokens conflict"
        )
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
    query_token = str(request.args.get("token") or "").strip()
    if _agent_token_file_reference():
        # File-managed service credentials must never enter URLs, proxy logs,
        # browser history or referrer headers.  The only query credential
        # accepted in this mode is the separately signed, short-lived user SSE
        # derivative on its exact GET route.
        return query_token if _is_valid_file_managed_stream_query_token(query_token) else None
    return query_token or None


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
            "User-JWT secret_key is shorter than 32 bytes; JWT validation remains enabled but is weakly configured."
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


def _user_token_allows_current_request(payload: Mapping[str, Any]) -> bool:
    if not token_scope_allows_request(
        payload,
        method=request.method,
        path=request.path,
    ):
        return False
    return not is_control_center_stream_token(payload) or control_center_stream_identity_is_bound(payload)


def _is_valid_file_managed_stream_query_token(token: str) -> bool:
    if not token or token.count(".") != 2:
        return False
    try:
        payload = _validate_user_jwt(token)
    except jwt.ExpiredSignatureError:
        return False
    return bool(
        payload
        and is_control_center_stream_token(payload)
        and control_center_stream_identity_is_bound(payload)
        and token_scope_allows_request(
            payload,
            method=request.method,
            path=request.path,
        )
    )


def _set_agent_admin_context(payload: dict | None = None) -> None:
    g.auth_payload = payload or {
        "sub": "agent_token",
        "role": "admin",
        "auth_mode": "agent_static_token",
    }
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
                if not _user_token_allows_current_request(user_payload):
                    return False, "user_token_scope_forbidden"
                _set_user_auth_context(user_payload)
                return True, "user_jwt"
        if allow_auth_disabled:
            logging.warning("Agent läuft OHNE Authentifizierung! Setzen Sie AGENT_TOKEN für mehr Sicherheit.")
            _set_agent_admin_context({"auth_mode": "auth_disabled"})
            return True, "auth_disabled"
        return False, "invalid_token"

    if not provided_token:
        return False, "missing_token"

    if provided_token.count(".") == 2:
        try:
            restricted_user_payload = _validate_user_jwt(provided_token)
        except jwt.ExpiredSignatureError:
            restricted_user_payload = None
        if restricted_user_payload and is_control_center_stream_token(restricted_user_payload):
            if not _user_token_allows_current_request(restricted_user_payload):
                return False, "user_token_scope_forbidden"
            _set_user_auth_context(restricted_user_payload)
            if require_admin and not getattr(g, "is_admin", False):
                return False, "admin_privileges_required"
            return True, "user_jwt"

    if agent_token:
        if provided_token.count(".") == 2:
            payload = _validate_agent_jwt(provided_token, agent_token)
            if payload:
                if is_control_center_stream_token(payload):
                    return False, "user_token_scope_forbidden"
                _set_agent_admin_context(
                    {
                        **payload,
                        "auth_mode": str(
                            payload.get("auth_mode")
                            or "agent_jwt"
                        ),
                    }
                )
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
        if not _user_token_allows_current_request(user_payload):
            return False, "user_token_scope_forbidden"
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

            if auth_mode == "user_token_scope_forbidden":
                return api_response(
                    status="error",
                    message="forbidden",
                    data={"reason_code": "user_token_scope_forbidden"},
                    code=403,
                )

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
        if auth_mode == "user_token_scope_forbidden":
            return api_response(
                status="error",
                message="forbidden",
                data={"reason_code": "user_token_scope_forbidden"},
                code=403,
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


def _strict_registered_worker_bearer_error(*, required_scope: str):
    """Authenticate a registered Worker without granting generic/admin auth."""

    from agent.services.repository_registry import get_repository_registry
    from agent.services.workflow_worker_service_auth import (
        WORKER_ID_HEADER,
        WORKER_URL_HEADER,
        WorkflowWorkerAuthConfigurationError,
        WorkflowWorkerAuthDenied,
        authenticate_registered_workflow_worker,
    )

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
    if not required_scope:
        logging.error("Strict registered Worker auth route has no service scope: %s", request.path)
        return api_response(
            status="error",
            message="service unavailable",
            data={"reason_code": "workflow_worker_auth_configuration_invalid"},
            code=503,
        )

    try:
        agents = get_repository_registry().agent_repo.get_all()
        hub_service_token = resolve_configured_agent_token(current_app.config)
        identity = authenticate_registered_workflow_worker(
            provided_token,
            required_scope=required_scope,
            claimed_worker_id=str(request.headers.get(WORKER_ID_HEADER) or ""),
            claimed_worker_url=str(request.headers.get(WORKER_URL_HEADER) or ""),
            registered_agents=agents or (),
            hub_service_token=hub_service_token,
            user_session_secret=current_app.secret_key,
            config=current_app.config,
        )
    except (WorkflowWorkerAuthConfigurationError, AgentTokenConfigurationError) as exc:
        logging.error("Strict registered Worker auth configuration rejected: %s", exc)
        return api_response(
            status="error",
            message="service unavailable",
            data={"reason_code": "workflow_worker_auth_configuration_invalid"},
            code=503,
        )
    except WorkflowWorkerAuthDenied as exc:
        _warn_auth_failure(exc.reason_code)
        log_audit(
            "workflow_worker_service_auth_denied",
            {
                "path": request.path,
                "method": request.method,
                "scope": required_scope,
                "reason_code": exc.reason_code,
            },
        )
        return api_response(
            status="error",
            message="forbidden" if exc.status_code == 403 else "unauthorized",
            data={"reason_code": exc.reason_code},
            code=exc.status_code,
        )
    except Exception:  # noqa: BLE001 - repository failures must fail closed.
        logging.exception("Registered Worker credential lookup failed")
        return api_response(
            status="error",
            message="service unavailable",
            data={"reason_code": "workflow_worker_directory_unavailable"},
            code=503,
        )

    g.user = {}
    g.auth_payload = identity.auth_payload(scope=required_scope)
    g.service_identity = {
        "worker_id": identity.worker_id,
        "worker_url": identity.worker_url,
        "capabilities": list(identity.capabilities),
    }
    # This is the essential separation from the historic shared AGENT_TOKEN:
    # the identity can never pass admin_required or a generic check_auth route.
    g.is_admin = False
    log_audit(
        "workflow_worker_service_authenticated",
        {
            "path": request.path,
            "method": request.method,
            "scope": required_scope,
            "worker_id": identity.worker_id,
            "worker_url": identity.worker_url,
        },
    )
    return None


def _strict_runtime_service_bearer_error(*, required_scope: str):
    """Authenticate one pre-provisioned non-Worker runtime service."""

    from agent.services.repository_registry import get_repository_registry
    from agent.services.workflow_worker_service_auth import (
        RUNTIME_SERVICE_ID_HEADER,
        WorkflowWorkerAuthConfigurationError,
        WorkflowWorkerAuthDenied,
        authenticate_preconfigured_runtime_service,
    )

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
    try:
        hub_service_token = resolve_configured_agent_token(current_app.config)
        registered_worker_tokens = tuple(
            str(getattr(agent, "token", "") or "")
            for agent in (
                get_repository_registry().agent_repo.get_all() or ()
            )
        )
        credential = authenticate_preconfigured_runtime_service(
            provided_token,
            required_scope=required_scope,
            claimed_service_id=str(
                request.headers.get(RUNTIME_SERVICE_ID_HEADER) or ""
            ),
            forbidden_token=hub_service_token,
            forbidden_tokens=registered_worker_tokens,
            forbidden_user_session_secret=current_app.secret_key,
            config=current_app.config,
        )
    except (WorkflowWorkerAuthConfigurationError, AgentTokenConfigurationError) as exc:
        logging.error("Scoped runtime service auth configuration rejected: %s", exc)
        return api_response(
            status="error",
            message="service unavailable",
            data={"reason_code": "workflow_runtime_service_auth_configuration_invalid"},
            code=503,
        )
    except WorkflowWorkerAuthDenied as exc:
        _warn_auth_failure(exc.reason_code)
        log_audit(
            "workflow_runtime_service_auth_denied",
            {
                "path": request.path,
                "method": request.method,
                "scope": required_scope,
                "reason_code": exc.reason_code,
            },
        )
        return api_response(
            status="error",
            message="forbidden" if exc.status_code == 403 else "unauthorized",
            data={"reason_code": exc.reason_code},
            code=exc.status_code,
        )
    except Exception:  # noqa: BLE001 - runtime identity lookup must fail closed.
        logging.exception("Scoped runtime service credential lookup failed")
        return api_response(
            status="error",
            message="service unavailable",
            data={"reason_code": "workflow_runtime_service_directory_unavailable"},
            code=503,
        )

    g.user = {}
    g.auth_payload = credential.auth_payload(scope=required_scope)
    g.service_identity = {
        "service_id": credential.service_id,
        "scopes": list(credential.scopes),
    }
    g.is_admin = False
    log_audit(
        "workflow_runtime_service_authenticated",
        {
            "path": request.path,
            "method": request.method,
            "scope": required_scope,
            "service_id": credential.service_id,
        },
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


def check_service_auth(
    f: Callable | None = None,
    *,
    scope: str = "",
):
    """Require a service credential, optionally narrowed to a Worker scope.

    Outside strict Native Production this preserves the historical agent
    bearer contract. With ``ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH=1``
    only an identity-bound registered Worker credential is accepted.
    """

    def decorator(target: Callable):
        @wraps(target)
        def wrapper(*args, **kwargs):
            from agent.services.workflow_worker_service_auth import (
                WORKFLOW_TEMPORAL_TASK_SCOPE,
                registered_worker_auth_required,
                runtime_service_keyring_configured,
            )

            normalized_scope = str(scope or "").strip()
            strict = registered_worker_auth_required(current_app.config)
            scoped_runtime = (
                normalized_scope == WORKFLOW_TEMPORAL_TASK_SCOPE
                and (strict or runtime_service_keyring_configured(current_app.config))
            )
            if scoped_runtime:
                error = _strict_runtime_service_bearer_error(
                    required_scope=normalized_scope
                )
            elif strict:
                error = _strict_registered_worker_bearer_error(
                    required_scope=normalized_scope
                )
            else:
                error = _strict_bearer_error(service_only=True)
            if error is not None:
                return error
            return target(*args, **kwargs)

        return wrapper

    if f is not None:
        return decorator(f)
    return decorator


def check_registered_worker_auth(
    f: Callable | None = None,
    *,
    scope: str = "",
):
    """Require an identity-bound registered Worker for one narrow scope.

    Unlike :func:`check_service_auth`, this decorator intentionally has no
    legacy shared-``AGENT_TOKEN`` fallback.  It is used by endpoints whose
    response is assigned to one concrete Worker identity.
    """

    def decorator(target: Callable):
        @wraps(target)
        def wrapper(*args, **kwargs):
            error = _strict_registered_worker_bearer_error(
                required_scope=str(scope or "").strip()
            )
            if error is not None:
                return error
            return target(*args, **kwargs)

        return wrapper

    if f is not None:
        return decorator(f)
    return decorator


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
            if not _user_token_allows_current_request(payload):
                return api_response(
                    status="error",
                    message="forbidden",
                    data={"reason_code": "user_token_scope_forbidden"},
                    code=403,
                )
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
                if auth_mode in {
                    "admin_privileges_required",
                    "user_token_scope_forbidden",
                }:
                    return api_response(
                        status="error", message="forbidden", data={"details": "Admin privileges required"}, code=403
                    )
                if auth_mode == "agent_token_file_invalid":
                    return api_response(
                        status="error",
                        message="service unavailable",
                        data={"reason_code": "auth_configuration_invalid"},
                        code=503,
                    )
                _warn_auth_failure(auth_mode or "invalid_token")
                return api_response(
                    status="error",
                    message="unauthorized",
                    data={"details": "Authentication required"},
                    code=401,
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
        raise PermanentError("Token-Rotation wird extern verwaltet, solange AGENT_TOKEN_FILE konfiguriert ist.")
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


def get_authenticated_source_control_principal():
    """Project the authenticated request identity into the Hub policy contract.

    Tenant, project, subject and roles are derived exclusively from the
    authenticated request context. Query parameters and request bodies are
    intentionally never consulted.
    """

    from agent.services.source_control_access_policy import HubSourcePrincipal

    context = get_request_auth_context()
    roles: set[str] = set()

    def _add_roles(value: Any) -> None:
        if isinstance(value, str):
            candidates = value.replace(",", " ").split()
        elif isinstance(value, (list, tuple, set, frozenset)):
            candidates = [str(item) for item in value]
        else:
            candidates = []
        roles.update(
            str(item).strip().lower().replace("-", "_")
            for item in candidates
            if str(item).strip()
        )

    _add_roles(context.get("role"))
    _add_roles(context.get("roles"))
    realm_access = context.get("realm_access")
    if isinstance(realm_access, Mapping):
        _add_roles(realm_access.get("roles"))
    if bool(getattr(g, "is_admin", False)):
        roles.add("admin")

    subject_id = str(
        context.get("sub")
        or context.get("subject_id")
        or context.get("user_id")
        or context.get("username")
        or ("hub_admin" if "admin" in roles else "")
    ).strip()
    tenant_id = str(
        context.get("tenant_id")
        or context.get("tenant")
        or context.get("tid")
        or ""
    ).strip()
    project_id = str(
        context.get("project_id")
        or context.get("project")
        or context.get("pid")
        or ""
    ).strip()
    return HubSourcePrincipal(
        subject_id=subject_id,
        tenant_id=tenant_id or None,
        project_id=project_id or None,
        roles=frozenset(roles),
    )
