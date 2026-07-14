"""OIDC Authorization Code Flow with PKCE for browser-based terminal access."""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from flask import Blueprint, g, jsonify, redirect, request, session
from werkzeug.security import generate_password_hash

from agent.auth import check_user_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.config import settings
from agent.db_models import UserDB
from agent.services.oidc_claims_mapper import map_claims_to_auth
from agent.services.oidc_identity_link_service import (
    OidcAccountProvisioningError,
    OidcAccountProvisioningUnavailableError,
    OidcIdentityLinkService,
    OidcIdentityValidationError,
    validate_oidc_external_identity,
)
from agent.services.oidc_settings import get_oidc_config, oidc_is_configured
from agent.services.oidc_validator import validate_oidc_token
from agent.services.user_session_tokens import (
    UserSessionIdentityError,
    issue_user_session_tokens,
    local_user_tenant_id,
)

LOGGER = logging.getLogger("agent.auth_oidc")

oidc_bp = Blueprint("auth_oidc", __name__)
_FRONTEND_TOKEN_EXCHANGE_CODES: dict[str, dict[str, Any]] = {}
_OIDC_LOGIN_REQUESTS: dict[str, dict[str, Any]] = {}
_OIDC_LOGIN_REQUESTS_LOCK = threading.Lock()
_OIDC_LOGIN_REQUEST_TTL_SECONDS = 300
_OIDC_LOGIN_REQUEST_LIMIT = 2048


class OidcAuthorizationFlowError(ValueError):
    """Stable fail-closed rejection for browser-bound authorization flows."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _identity_link_service() -> OidcIdentityLinkService:
    from agent.services.repository_registry import get_repository_registry

    repos = get_repository_registry()
    return OidcIdentityLinkService(repos.oidc_identity_link_repo, repos.user_repo)


def _map_claims_to_auth(claims: dict[str, Any]) -> dict[str, Any]:
    return map_claims_to_auth(claims)


def _first_present_identity(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return ""


def _validated_oidc_auth_context(claims: dict[str, Any]) -> dict[str, Any]:
    """Map verified claims while retaining strict, uncoerced identities."""

    identity = validate_oidc_external_identity(
        issuer=claims.get("iss"),
        subject=claims.get("sub"),
    )
    auth_ctx = _map_claims_to_auth(claims)
    username = _first_present_identity(
        claims.get("email"),
        claims.get("preferred_username"),
        identity.subject,
    )
    auth_ctx["sub"] = identity.subject
    auth_ctx["issuer"] = identity.issuer
    auth_ctx["username"] = username
    auth_ctx["email"] = username
    return auth_ctx


def _validated_stored_auth_context(auth_ctx: dict[str, Any]) -> dict[str, Any]:
    identity = validate_oidc_external_identity(
        issuer=_first_present_identity(auth_ctx.get("issuer"), auth_ctx.get("iss")),
        subject=auth_ctx.get("sub"),
    )
    validated = dict(auth_ctx)
    validated["issuer"] = identity.issuer
    validated["sub"] = identity.subject
    return validated


def _ensure_local_user_account(auth_ctx: dict[str, Any]) -> UserDB:
    identity = validate_oidc_external_identity(
        issuer=auth_ctx.get("issuer"),
        subject=auth_ctx.get("sub"),
    )
    username = _first_present_identity(
        auth_ctx.get("username"),
        auth_ctx.get("email"),
        identity.subject,
    )
    role = str(auth_ctx.get("role") or "viewer").strip() or "viewer"
    return _identity_link_service().resolve_or_provision(
        username=username,
        issuer=identity.issuer,
        subject=identity.subject,
        role=role,
        password_hash=generate_password_hash(secrets.token_urlsafe(48)),
    )


def _oidc_identity_rejection_response(
    exc: Exception,
    *,
    phase: str,
):
    reason_code = str(getattr(exc, "reason_code", "") or str(exc) or "oidc_identity_rejected")
    log_audit(
        "oidc_identity_rejected",
        {
            "endpoint": request.endpoint or "",
            "phase": phase,
            "reason_code": reason_code,
        },
    )
    LOGGER.warning("OIDC identity rejected during %s: %s", phase, reason_code)
    return api_response(
        status="error",
        message=reason_code,
        data={"reason_code": reason_code},
        code=409,
    )


def _oidc_authorization_flow_rejection_response(
    exc: OidcAuthorizationFlowError,
    *,
    phase: str,
):
    reason_code = exc.reason_code
    log_audit(
        "oidc_authorization_flow_rejected",
        {
            "endpoint": request.endpoint or "",
            "phase": phase,
            "reason_code": reason_code,
        },
    )
    LOGGER.warning("OIDC authorization flow rejected during %s: %s", phase, reason_code)
    return api_response(
        status="error",
        message=reason_code,
        data={"reason_code": reason_code},
        code=401,
    )


def _oidc_provisioning_unavailable_response(
    exc: OidcAccountProvisioningUnavailableError,
    *,
    phase: str,
):
    reason_code = exc.reason_code
    log_audit(
        "oidc_identity_provisioning_unavailable",
        {
            "endpoint": request.endpoint or "",
            "phase": phase,
            "reason_code": reason_code,
        },
    )
    LOGGER.error("OIDC identity provisioning unavailable during %s", phase)
    return api_response(
        status="error",
        message=reason_code,
        data={"reason_code": reason_code},
        code=503,
    )


def _fetch_oidc_discovery(issuer: str) -> dict[str, Any]:
    import urllib.request
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
            import json
            return json.loads(resp.read().decode())
    except Exception as exc:
        raise RuntimeError("oidc_discovery_failed") from exc


def _public_authorization_endpoint(
    authorization_endpoint: str,
    *,
    internal_issuer: str,
    browser_issuer: str,
) -> str:
    if not browser_issuer:
        return authorization_endpoint

    try:
        ep = urlsplit(authorization_endpoint)
        internal = urlsplit(internal_issuer)
        public = urlsplit(browser_issuer)
    except Exception:
        return authorization_endpoint

    if ep.netloc != internal.netloc:
        return authorization_endpoint

    return urlunsplit((public.scheme, public.netloc, ep.path, ep.query, ep.fragment))


def _oidc_redirect_uri() -> str:
    return request.host_url.rstrip("/") + "/auth/oidc/callback"


def _pkce_s256_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _store_frontend_exchange_code(auth_ctx: dict[str, Any], redirect_path: str) -> str:
    code = secrets.token_urlsafe(32)
    _FRONTEND_TOKEN_EXCHANGE_CODES[code] = {
        "auth_ctx": auth_ctx,
        "redirect_path": redirect_path or "/",
        "oidc_access_token": "",
        "expires_at": time.time() + 120,
    }
    return code


def _consume_frontend_exchange_code(code: str) -> dict[str, Any] | None:
    payload = _FRONTEND_TOKEN_EXCHANGE_CODES.pop(code, None)
    if not payload:
        return None
    if float(payload.get("expires_at") or 0.0) < time.time():
        return None
    return payload


def _store_oidc_login_request(
    *,
    state: str,
    nonce: str,
    code_verifier: str,
    redirect_path: str,
    browser_session_id: str,
) -> None:
    now = time.time()
    with _OIDC_LOGIN_REQUESTS_LOCK:
        expired_states = [
            candidate_state
            for candidate_state, payload in _OIDC_LOGIN_REQUESTS.items()
            if float(payload.get("expires_at") or 0.0) < now
        ]
        for expired_state in expired_states:
            _OIDC_LOGIN_REQUESTS.pop(expired_state, None)
        while len(_OIDC_LOGIN_REQUESTS) >= _OIDC_LOGIN_REQUEST_LIMIT:
            oldest_state = min(
                _OIDC_LOGIN_REQUESTS,
                key=lambda candidate: float(
                    _OIDC_LOGIN_REQUESTS[candidate].get("expires_at") or 0.0
                ),
            )
            _OIDC_LOGIN_REQUESTS.pop(oldest_state, None)
        _OIDC_LOGIN_REQUESTS[state] = {
            "nonce": nonce,
            "code_verifier": code_verifier,
            "redirect_path": redirect_path or "/",
            "browser_session_id": browser_session_id,
            "expires_at": now + _OIDC_LOGIN_REQUEST_TTL_SECONDS,
        }


def _consume_oidc_login_request(state: str) -> dict[str, Any] | None:
    with _OIDC_LOGIN_REQUESTS_LOCK:
        payload = _OIDC_LOGIN_REQUESTS.pop(state, None)
    if not payload:
        return None
    if float(payload.get("expires_at") or 0.0) < time.time():
        return None
    return payload


def _clear_oidc_session_request() -> None:
    for key in (
        "oidc_state",
        "oidc_nonce",
        "oidc_code_verifier",
        "oidc_redirect_path",
        "oidc_browser_session_id",
    ):
        session.pop(key, None)


def _constant_time_equal(left: str, right: str) -> bool:
    return bool(left and right) and secrets.compare_digest(
        left.encode("utf-8"),
        right.encode("utf-8"),
    )


def _consume_browser_bound_oidc_login_request(state: str) -> dict[str, Any]:
    """Consume one authorization request bound to this exact browser session.

    State is first matched against the signed browser session. Only that
    browser can consume the server-side record, preventing a state learned in
    one client from becoming a cross-client compatibility escape hatch.
    """

    if not state:
        raise OidcAuthorizationFlowError("oidc_state_missing")

    session_state = session.get("oidc_state")
    if not isinstance(session_state, str) or not session_state:
        raise OidcAuthorizationFlowError("oidc_session_state_missing")
    if not _constant_time_equal(state, session_state):
        raise OidcAuthorizationFlowError("oidc_state_mismatch")

    browser_session_id = session.get("oidc_browser_session_id")
    if not isinstance(browser_session_id, str) or not browser_session_id:
        raise OidcAuthorizationFlowError("oidc_browser_session_missing")

    login_request = _consume_oidc_login_request(state)
    session_nonce = session.get("oidc_nonce")
    session_code_verifier = session.get("oidc_code_verifier")
    _clear_oidc_session_request()

    if login_request is None:
        raise OidcAuthorizationFlowError("oidc_state_unknown_or_replayed")

    stored_browser_session_id = login_request.get("browser_session_id")
    if not isinstance(stored_browser_session_id, str) or not _constant_time_equal(
        browser_session_id,
        stored_browser_session_id,
    ):
        raise OidcAuthorizationFlowError("oidc_browser_session_mismatch")

    stored_nonce = login_request.get("nonce")
    if (
        not isinstance(session_nonce, str)
        or not session_nonce
        or not isinstance(stored_nonce, str)
        or not stored_nonce
    ):
        raise OidcAuthorizationFlowError("oidc_nonce_missing")
    if not _constant_time_equal(session_nonce, stored_nonce):
        raise OidcAuthorizationFlowError("oidc_nonce_mismatch")

    stored_code_verifier = login_request.get("code_verifier")
    if (
        not isinstance(session_code_verifier, str)
        or not session_code_verifier
        or not isinstance(stored_code_verifier, str)
        or not stored_code_verifier
    ):
        raise OidcAuthorizationFlowError("oidc_code_verifier_missing")
    if not _constant_time_equal(session_code_verifier, stored_code_verifier):
        raise OidcAuthorizationFlowError("oidc_code_verifier_mismatch")
    return login_request


def _validate_id_token(token: str, *, issuer: str, audience: str, nonce: str) -> dict[str, Any]:
    try:
        import jwt as pyjwt
    except ImportError as exc:
        raise RuntimeError("oidc_pyjwt_missing") from exc

    discovery = _fetch_oidc_discovery(issuer)
    jwks_uri = discovery.get("jwks_uri")
    if not jwks_uri:
        raise ValueError("oidc_jwks_uri_missing")

    jwks_client = pyjwt.PyJWKClient(jwks_uri)
    signing_key = jwks_client.get_signing_key_from_jwt(token)
    claims = pyjwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256", "ES256"],
        audience=audience,
        issuer=issuer,
        options={"require": ["sub", "iss", "aud", "exp", "iat"], "leeway": 60},
    )
    claim_nonce = claims.get("nonce")
    if not isinstance(claim_nonce, str) or not claim_nonce:
        raise OidcAuthorizationFlowError("oidc_nonce_missing")
    if not _constant_time_equal(claim_nonce, nonce):
        raise OidcAuthorizationFlowError("oidc_nonce_mismatch")
    return claims


@oidc_bp.route("/auth/oidc/login", methods=["GET"])
def oidc_login():
    if not settings.terminal_oidc_enabled:
        return api_response(status="error", message="oidc_not_enabled", code=404)

    issuer = settings.terminal_oidc_issuer
    client_id = settings.terminal_oidc_client_id
    if not issuer or not client_id:
        return api_response(status="error", message="oidc_not_configured", code=503)

    try:
        discovery = _fetch_oidc_discovery(issuer)
    except RuntimeError:
        return api_response(
            status="error",
            message="oidc_discovery_failed",
            data={"reason_code": "oidc_discovery_failed"},
            code=503,
        )

    auth_endpoint = discovery.get("authorization_endpoint")
    if not auth_endpoint:
        return api_response(status="error", message="oidc_auth_endpoint_missing", code=503)
    auth_endpoint = _public_authorization_endpoint(
        auth_endpoint,
        internal_issuer=issuer,
        browser_issuer=settings.terminal_oidc_browser_issuer,
    )

    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(48)
    code_challenge = _pkce_s256_challenge(code_verifier)
    browser_session_id = secrets.token_urlsafe(32)

    previous_state = session.get("oidc_state")
    if isinstance(previous_state, str) and previous_state:
        _consume_oidc_login_request(previous_state)

    session["oidc_state"] = state
    session["oidc_nonce"] = nonce
    session["oidc_code_verifier"] = code_verifier
    session["oidc_browser_session_id"] = browser_session_id
    session["oidc_redirect_path"] = request.args.get("redirect_path") or "/"
    _store_oidc_login_request(
        state=state,
        nonce=nonce,
        code_verifier=code_verifier,
        redirect_path=str(session["oidc_redirect_path"] or "/"),
        browser_session_id=browser_session_id,
    )

    redirect_uri = _oidc_redirect_uri()
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return redirect(f"{auth_endpoint}?{urlencode(params)}")


@oidc_bp.route("/auth/oidc/callback", methods=["GET"])
def oidc_callback():
    if not settings.terminal_oidc_enabled:
        return api_response(status="error", message="oidc_not_enabled", code=404)

    state = request.args.get("state")
    code = request.args.get("code")
    error = request.args.get("error")

    try:
        login_request = _consume_browser_bound_oidc_login_request(state or "")
    except OidcAuthorizationFlowError as exc:
        return _oidc_authorization_flow_rejection_response(exc, phase="callback")

    if error:
        LOGGER.warning("OIDC error from provider: %s", error)
        return api_response(status="error", message=f"oidc_provider_error: {error}", code=401)

    if not code:
        return api_response(status="error", message="oidc_code_missing", code=401)

    issuer = settings.terminal_oidc_issuer
    client_id = settings.terminal_oidc_client_id
    client_secret = str(settings.terminal_oidc_client_secret or "").strip()
    # Keycloak id_token audience is the OIDC client itself, not the downstream hub JWT audience.
    audience = client_id
    nonce = str(login_request["nonce"])
    code_verifier = str(login_request["code_verifier"])

    try:
        discovery = _fetch_oidc_discovery(issuer)
        token_endpoint = discovery.get("token_endpoint")
        if not token_endpoint:
            raise ValueError("oidc_token_endpoint_missing")

        import urllib.parse
        import urllib.request
        redirect_uri = _oidc_redirect_uri()
        post_data = urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            **({"client_secret": client_secret} if client_secret else {}),
            "code_verifier": code_verifier or "",
        }).encode()
        req = urllib.request.Request(token_endpoint, data=post_data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            import json
            token_response = json.loads(resp.read().decode())

        id_token = token_response.get("id_token")
        if not id_token:
            raise ValueError("oidc_id_token_missing")

        claims = _validate_id_token(id_token, issuer=issuer, audience=audience, nonce=nonce)
        auth_ctx = _validated_oidc_auth_context(claims)
        oidc_access_token = str(token_response.get("access_token") or "").strip()

        session["user"] = auth_ctx
        LOGGER.info("OIDC login successful for sub=%s role=%s", auth_ctx.get("sub"), auth_ctx.get("role"))
        frontend_redirect = settings.terminal_oidc_frontend_redirect.strip()
        if frontend_redirect:
            redirect_path = str(
                login_request.get("redirect_path")
                or "/"
            )
            code = _store_frontend_exchange_code(auth_ctx, redirect_path)
            _FRONTEND_TOKEN_EXCHANGE_CODES[code]["oidc_access_token"] = oidc_access_token
            return redirect(f"{frontend_redirect}{'&' if '?' in frontend_redirect else '?'}oidc_code={code}")
        return jsonify({"ok": True, "auth": auth_ctx})

    except OidcAuthorizationFlowError as exc:
        return _oidc_authorization_flow_rejection_response(exc, phase="callback")
    except (OidcIdentityValidationError, UserSessionIdentityError) as exc:
        return _oidc_identity_rejection_response(exc, phase="callback")
    except Exception as exc:
        LOGGER.warning(
            "OIDC callback failed (exception_type=%s)",
            type(exc).__name__,
        )
        return api_response(
            status="error",
            message="oidc_callback_failed",
            data={"reason_code": "oidc_callback_failed"},
            code=401,
        )


@oidc_bp.route("/auth/oidc/exchange", methods=["GET", "POST"])
def oidc_exchange():
    if not settings.terminal_oidc_enabled and not oidc_is_configured():
        return api_response(status="error", message="oidc_not_enabled", code=404)

    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    code = str(request.args.get("code") or body.get("code") or "").strip()
    state = str(request.args.get("state") or body.get("state") or "").strip()
    direct_access_token = str(request.args.get("oidc_access_token") or body.get("oidc_access_token") or "").strip()
    direct_redirect_path = str(request.args.get("redirect_path") or body.get("redirect_path") or "/").strip() or "/"

    # Modern account-link exchange.  The OIDC token is validated only at this
    # explicit boundary and is never accepted by @check_user_auth directly.
    if direct_access_token and get_oidc_config().enabled and not oidc_is_configured():
        return api_response(status="error", message="oidc_linking_not_configured", code=503)
    if direct_access_token and oidc_is_configured():
        cfg = get_oidc_config()
        claims = validate_oidc_token(direct_access_token, cfg)
        if claims is None:
            return api_response(status="error", message="invalid_oidc_token", code=401)
        try:
            identity = validate_oidc_external_identity(
                issuer=claims.get("iss"),
                subject=claims.get("sub"),
            )
            linked_user = _identity_link_service().resolve(
                issuer=identity.issuer,
                subject=identity.subject,
            )
        except (OidcIdentityValidationError, UserSessionIdentityError) as exc:
            return _oidc_identity_rejection_response(exc, phase="linked_exchange")
        if linked_user is None:
            return _oidc_identity_rejection_response(
                OidcAccountProvisioningError("oidc_identity_not_linked"),
                phase="linked_exchange",
            )
        try:
            tokens = issue_user_session_tokens(
                username=linked_user.username,
                role=linked_user.role,
                mfa_enabled=linked_user.mfa_enabled,
            )
        except UserSessionIdentityError as exc:
            return _oidc_identity_rejection_response(exc, phase="linked_exchange")
        log_audit(
            "oidc_link_session_exchanged",
            {
                "username": linked_user.username,
                "issuer": identity.issuer,
                "subject": identity.subject,
            },
        )
        tokens["redirect_path"] = direct_redirect_path
        return jsonify({"ok": True, "data": tokens})

    if not code:
        return api_response(status="error", message="oidc_code_missing", code=400)

    payload = _consume_frontend_exchange_code(code)
    if payload:
        try:
            auth_ctx = _validated_stored_auth_context(payload.get("auth_ctx") or {})
            local_user = _ensure_local_user_account(auth_ctx)
            tokens = issue_user_session_tokens(
                username=local_user.username,
                role=local_user.role,
                mfa_enabled=local_user.mfa_enabled,
            )
        except OidcAccountProvisioningUnavailableError as exc:
            return _oidc_provisioning_unavailable_response(
                exc,
                phase="frontend_exchange",
            )
        except (
            OidcAccountProvisioningError,
            OidcIdentityValidationError,
            UserSessionIdentityError,
        ) as exc:
            return _oidc_identity_rejection_response(exc, phase="frontend_exchange")
        tokens["redirect_path"] = payload.get("redirect_path") or "/"
        oidc_access_token = str(payload.get("oidc_access_token") or "").strip()
        if oidc_access_token:
            tokens["oidc_access_token"] = oidc_access_token
        return jsonify({"ok": True, "data": tokens})

    issuer = settings.terminal_oidc_issuer
    client_id = settings.terminal_oidc_client_id
    client_secret = str(settings.terminal_oidc_client_secret or "").strip()
    # Keycloak id_token audience is the OIDC client itself, not the downstream hub JWT audience.
    audience = client_id
    if not issuer or not client_id:
        return api_response(status="error", message="oidc_not_configured", code=503)

    try:
        login_request = _consume_browser_bound_oidc_login_request(state)
    except OidcAuthorizationFlowError as exc:
        return _oidc_authorization_flow_rejection_response(exc, phase="code_exchange")
    nonce = str(login_request["nonce"])
    code_verifier = str(login_request["code_verifier"])

    try:
        discovery = _fetch_oidc_discovery(issuer)
        token_endpoint = discovery.get("token_endpoint")
        if not token_endpoint:
            raise ValueError("oidc_token_endpoint_missing")

        import urllib.parse
        import urllib.request
        redirect_uri = _oidc_redirect_uri()
        post_data = urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            **({"client_secret": client_secret} if client_secret else {}),
            "code_verifier": code_verifier or "",
        }).encode()
        req = urllib.request.Request(token_endpoint, data=post_data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            import json
            token_response = json.loads(resp.read().decode())

        id_token = token_response.get("id_token")
        if not id_token:
            raise ValueError("oidc_id_token_missing")

        claims = _validate_id_token(id_token, issuer=issuer, audience=audience, nonce=nonce)
        auth_ctx = _validated_oidc_auth_context(claims)
        local_user = _ensure_local_user_account(auth_ctx)

        LOGGER.info("OIDC code exchange successful for sub=%s role=%s", auth_ctx.get("sub"), auth_ctx.get("role"))
        tokens = issue_user_session_tokens(
            username=local_user.username,
            role=local_user.role,
            mfa_enabled=local_user.mfa_enabled,
        )
        session["user"] = auth_ctx
        tokens["redirect_path"] = str(login_request.get("redirect_path") or "/")
        tokens["oidc_access_token"] = str(token_response.get("access_token") or "").strip()
        return jsonify({"ok": True, "data": tokens})

    except OidcAuthorizationFlowError as exc:
        return _oidc_authorization_flow_rejection_response(exc, phase="code_exchange")
    except OidcAccountProvisioningUnavailableError as exc:
        return _oidc_provisioning_unavailable_response(
            exc,
            phase="code_exchange",
        )
    except (
        OidcAccountProvisioningError,
        OidcIdentityValidationError,
        UserSessionIdentityError,
    ) as exc:
        return _oidc_identity_rejection_response(exc, phase="code_exchange")
    except Exception as exc:
        LOGGER.warning(
            "OIDC code exchange failed (exception_type=%s)",
            type(exc).__name__,
        )
        return api_response(
            status="error",
            message="oidc_code_exchange_failed",
            data={"reason_code": "oidc_code_exchange_failed"},
            code=401,
        )


@oidc_bp.route("/auth/oidc/link", methods=["GET", "POST", "DELETE"])
@check_user_auth
def oidc_identity_link():
    """Manage the current Hub user's explicit Keycloak account link."""

    if not oidc_is_configured():
        return api_response(status="error", message="oidc_linking_not_configured", code=404)

    cfg = get_oidc_config()
    raw_username = _first_present_identity(
        (g.user or {}).get("sub"),
        (g.user or {}).get("username"),
    )
    try:
        username = local_user_tenant_id(raw_username)
    except UserSessionIdentityError as exc:
        return _oidc_identity_rejection_response(exc, phase="hub_account_link")

    if request.method == "GET":
        try:
            link = _identity_link_service().status(username=username, issuer=cfg.issuer_url)
        except (OidcIdentityValidationError, UserSessionIdentityError) as exc:
            return _oidc_identity_rejection_response(exc, phase="link_status")
        return jsonify({
            "ok": True,
            "data": {
                "linked": link is not None,
                "issuer": cfg.issuer_url,
                "subject": link.subject if link else None,
            },
        })

    if request.method == "DELETE":
        try:
            removed = _identity_link_service().unlink(username=username, issuer=cfg.issuer_url)
        except (OidcIdentityValidationError, UserSessionIdentityError) as exc:
            return _oidc_identity_rejection_response(exc, phase="unlink")
        if removed:
            log_audit("oidc_identity_unlinked", {"username": username, "issuer": cfg.issuer_url})
        return jsonify({"ok": True, "data": {"linked": False, "removed": removed}})

    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    oidc_access_token = str(body.get("oidc_access_token") or "").strip()
    claims = validate_oidc_token(oidc_access_token, cfg) if oidc_access_token else None
    if claims is None:
        return api_response(status="error", message="invalid_oidc_token", code=401)
    try:
        identity = validate_oidc_external_identity(
            issuer=claims.get("iss"),
            subject=claims.get("sub"),
        )
        link = _identity_link_service().link(
            username=username,
            issuer=identity.issuer,
            subject=identity.subject,
        )
    except (OidcIdentityValidationError, UserSessionIdentityError) as exc:
        return _oidc_identity_rejection_response(exc, phase="link")
    except ValueError as exc:
        return _oidc_identity_rejection_response(exc, phase="link")
    log_audit(
        "oidc_identity_linked",
        {"username": link.username, "issuer": link.issuer, "subject": link.subject},
    )
    return jsonify({
        "ok": True,
        "data": {
            "linked": True,
            "issuer": link.issuer,
            "subject": link.subject,
            "username": link.username,
        },
    })


@oidc_bp.route("/auth/oidc/userinfo", methods=["GET"])
def oidc_userinfo():
    user = session.get("user")
    if not user:
        return api_response(status="error", message="not_authenticated", code=401)
    return jsonify({"ok": True, "user": user})


@oidc_bp.route("/auth/oidc/logout", methods=["POST"])
def oidc_logout():
    session.pop("user", None)
    return jsonify({"ok": True})
