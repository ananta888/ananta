"""OIDC Authorization Code Flow with PKCE for browser-based terminal access."""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
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
from agent.services.oidc_identity_link_service import OidcIdentityLinkService
from agent.services.oidc_settings import get_oidc_config, oidc_is_configured
from agent.services.oidc_validator import validate_oidc_token
from agent.services.user_session_tokens import issue_user_session_tokens

LOGGER = logging.getLogger("agent.auth_oidc")

oidc_bp = Blueprint("auth_oidc", __name__)
_FRONTEND_TOKEN_EXCHANGE_CODES: dict[str, dict[str, Any]] = {}
_OIDC_LOGIN_REQUESTS: dict[str, dict[str, Any]] = {}


def _identity_link_service() -> OidcIdentityLinkService:
    from agent.services.repository_registry import get_repository_registry

    repos = get_repository_registry()
    return OidcIdentityLinkService(repos.oidc_identity_link_repo, repos.user_repo)

def _map_claims_to_auth(claims: dict[str, Any]) -> dict[str, Any]:
    return map_claims_to_auth(claims)


def _ensure_local_user_account(auth_ctx: dict[str, Any]) -> None:
    username = str(auth_ctx.get("username") or auth_ctx.get("email") or auth_ctx.get("sub") or "").strip()
    if not username:
        return

    from agent.services.repository_registry import get_repository_registry

    user_repo = get_repository_registry().user_repo

    existing = user_repo.get_by_username(username)
    if existing:
        return

    role = str(auth_ctx.get("role") or "viewer").strip() or "viewer"
    user_repo.save(
        UserDB(
            username=username,
            password_hash=generate_password_hash(secrets.token_urlsafe(48)),
            role=role,
            mfa_secret=None,
            mfa_enabled=False,
            mfa_backup_codes=[],
            failed_login_attempts=0,
            lockout_until=None,
        )
    )


def _fetch_oidc_discovery(issuer: str) -> dict[str, Any]:
    import urllib.request
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
            import json
            return json.loads(resp.read().decode())
    except Exception as exc:
        raise RuntimeError(f"oidc_discovery_failed: {exc}") from exc


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
) -> None:
    _OIDC_LOGIN_REQUESTS[state] = {
        "nonce": nonce,
        "code_verifier": code_verifier,
        "redirect_path": redirect_path or "/",
        "expires_at": time.time() + 300,
    }


def _consume_oidc_login_request(state: str) -> dict[str, Any] | None:
    payload = _OIDC_LOGIN_REQUESTS.pop(state, None)
    if not payload:
        return None
    if float(payload.get("expires_at") or 0.0) < time.time():
        return None
    return payload


def _validate_id_token(token: str, *, issuer: str, audience: str, nonce: str | None = None) -> dict[str, Any]:
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
    if nonce and claims.get("nonce") != nonce:
        raise ValueError("oidc_nonce_mismatch")
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
    except RuntimeError as exc:
        return api_response(status="error", message=str(exc), code=503)

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

    session["oidc_state"] = state
    session["oidc_nonce"] = nonce
    session["oidc_code_verifier"] = code_verifier
    session["oidc_redirect_path"] = request.args.get("redirect_path") or "/"
    _store_oidc_login_request(
        state=state,
        nonce=nonce,
        code_verifier=code_verifier,
        redirect_path=str(session["oidc_redirect_path"] or "/"),
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

    if error:
        LOGGER.warning("OIDC error from provider: %s", error)
        return api_response(status="error", message=f"oidc_provider_error: {error}", code=401)

    login_request = _consume_oidc_login_request(state) if state else None
    session_state = str(session.get("oidc_state") or "").strip()
    if not login_request and (not state or state != session_state):
        LOGGER.warning(
            "OIDC callback rejected due to state mismatch (got=%s, session_present=%s)",
            state or "",
            bool(session_state),
        )
        return api_response(status="error", message="oidc_state_mismatch", code=401)

    if not code:
        return api_response(status="error", message="oidc_code_missing", code=401)

    issuer = settings.terminal_oidc_issuer
    client_id = settings.terminal_oidc_client_id
    client_secret = str(settings.terminal_oidc_client_secret or "").strip()
    # Keycloak id_token audience is the OIDC client itself, not the downstream hub JWT audience.
    audience = client_id
    nonce = login_request.get("nonce") if login_request else session.pop("oidc_nonce", None)
    code_verifier = login_request.get("code_verifier") if login_request else session.pop("oidc_code_verifier", None)
    session.pop("oidc_state", None)
    if not code_verifier:
        LOGGER.warning("OIDC callback rejected because code_verifier is missing from the session")
        return api_response(status="error", message="oidc_code_verifier_missing", code=401)

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
        auth_ctx = _map_claims_to_auth(claims)
        oidc_access_token = str(token_response.get("access_token") or "").strip()

        session["user"] = auth_ctx
        LOGGER.info("OIDC login successful for sub=%s role=%s", auth_ctx.get("sub"), auth_ctx.get("role"))
        frontend_redirect = settings.terminal_oidc_frontend_redirect.strip()
        if frontend_redirect:
            redirect_path = str(
                (login_request or {}).get("redirect_path")
                or session.pop("oidc_redirect_path", "/")
                or "/"
            )
            code = _store_frontend_exchange_code(auth_ctx, redirect_path)
            _FRONTEND_TOKEN_EXCHANGE_CODES[code]["oidc_access_token"] = oidc_access_token
            return redirect(f"{frontend_redirect}{'&' if '?' in frontend_redirect else '?'}oidc_code={code}")
        return jsonify({"ok": True, "auth": auth_ctx})

    except Exception as exc:
        detail = ""
        try:
            from urllib.error import HTTPError
            if isinstance(exc, HTTPError):
                detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        print(f"OIDC callback exception: {exc!r} body={detail!r}", flush=True)
        LOGGER.warning("OIDC callback failed: %s", exc)
        return api_response(status="error", message=str(exc), code=401)


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
        linked_user = _identity_link_service().resolve(
            issuer=str(claims.get("iss") or ""),
            subject=str(claims.get("sub") or ""),
        )
        if linked_user is None:
            return api_response(status="error", message="oidc_identity_not_linked", code=409)
        tokens = issue_user_session_tokens(
            username=linked_user.username,
            role=linked_user.role,
            mfa_enabled=linked_user.mfa_enabled,
        )
        log_audit(
            "oidc_link_session_exchanged",
            {"username": linked_user.username, "issuer": claims.get("iss"), "subject": claims.get("sub")},
        )
        tokens["redirect_path"] = direct_redirect_path
        return jsonify({"ok": True, "data": tokens})

    if not code:
        return api_response(status="error", message="oidc_code_missing", code=400)

    payload = _consume_frontend_exchange_code(code)
    if payload:
        auth_ctx = payload.get("auth_ctx") or {}
        username = str(auth_ctx.get("username") or auth_ctx.get("email") or auth_ctx.get("sub") or "").strip()
        role = str(auth_ctx.get("role") or "viewer").strip() or "viewer"
        if not username:
            return api_response(status="error", message="oidc_username_missing", code=401)

        _ensure_local_user_account(auth_ctx)

        tokens = issue_user_session_tokens(
            username=username,
            role=role,
            mfa_enabled=bool(auth_ctx.get("mfa_enabled")),
        )
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

    session_state = str(session.get("oidc_state") or "").strip()
    if state and session_state and state != session_state:
        return api_response(status="error", message="oidc_state_mismatch", code=401)

    nonce = session.get("oidc_nonce")
    code_verifier = session.get("oidc_code_verifier")
    if not code_verifier:
        LOGGER.warning("OIDC exchange rejected because code_verifier is missing from the session")
        return api_response(status="error", message="oidc_code_verifier_missing", code=401)

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
        auth_ctx = _map_claims_to_auth(claims)
        session["user"] = auth_ctx
        _ensure_local_user_account(auth_ctx)
        session.pop("oidc_state", None)
        session.pop("oidc_nonce", None)
        session.pop("oidc_code_verifier", None)

        LOGGER.info("OIDC code exchange successful for sub=%s role=%s", auth_ctx.get("sub"), auth_ctx.get("role"))
        tokens = issue_user_session_tokens(
            username=str(auth_ctx.get("username") or auth_ctx.get("email") or auth_ctx.get("sub") or "").strip(),
            role=str(auth_ctx.get("role") or "viewer").strip() or "viewer",
            mfa_enabled=bool(auth_ctx.get("mfa_enabled")),
        )
        tokens["redirect_path"] = str(session.pop("oidc_redirect_path", "/") or "/")
        tokens["oidc_access_token"] = str(token_response.get("access_token") or "").strip()
        return jsonify({"ok": True, "data": tokens})

    except Exception as exc:
        detail = ""
        try:
            from urllib.error import HTTPError
            if isinstance(exc, HTTPError):
                detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        print(f"OIDC exchange exception: {exc!r} body={detail!r}", flush=True)
        LOGGER.warning("OIDC exchange failed: %s", exc)
        return api_response(status="error", message=str(exc), code=401)


@oidc_bp.route("/auth/oidc/link", methods=["GET", "POST", "DELETE"])
@check_user_auth
def oidc_identity_link():
    """Manage the current Hub user's explicit Keycloak account link."""

    if not oidc_is_configured():
        return api_response(status="error", message="oidc_linking_not_configured", code=404)

    cfg = get_oidc_config()
    username = str((g.user or {}).get("sub") or (g.user or {}).get("username") or "").strip()
    if not username:
        return api_response(status="error", message="hub_user_missing", code=401)

    if request.method == "GET":
        link = _identity_link_service().status(username=username, issuer=cfg.issuer_url)
        return jsonify({
            "ok": True,
            "data": {
                "linked": link is not None,
                "issuer": cfg.issuer_url,
                "subject": link.subject if link else None,
            },
        })

    if request.method == "DELETE":
        removed = _identity_link_service().unlink(username=username, issuer=cfg.issuer_url)
        if removed:
            log_audit("oidc_identity_unlinked", {"username": username, "issuer": cfg.issuer_url})
        return jsonify({"ok": True, "data": {"linked": False, "removed": removed}})

    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    oidc_access_token = str(body.get("oidc_access_token") or "").strip()
    claims = validate_oidc_token(oidc_access_token, cfg) if oidc_access_token else None
    if claims is None:
        return api_response(status="error", message="invalid_oidc_token", code=401)
    try:
        link = _identity_link_service().link(
            username=username,
            issuer=str(claims.get("iss") or ""),
            subject=str(claims.get("sub") or ""),
        )
    except ValueError as exc:
        return api_response(status="error", message=str(exc), code=409)
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
