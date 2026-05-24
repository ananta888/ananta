"""OIDC Authorization Code Flow with PKCE for browser-based terminal access."""
from __future__ import annotations

import hashlib
import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode
from urllib.parse import urlsplit, urlunsplit

from flask import Blueprint, current_app, jsonify, redirect, request, session

from agent.common.errors import api_response
from agent.config import settings

LOGGER = logging.getLogger("agent.auth_oidc")

oidc_bp = Blueprint("auth_oidc", __name__)

# ── claim-to-role mapping ──────────────────────────────────────────────────────
_DEFAULT_CLAIM_ROLE_MAP: dict[str, str] = {
    "ananta-admin": "admin",
    "ananta-user": "user",
    "ananta-viewer": "viewer",
}

_DEFAULT_TERMINAL_PERMISSION_MAP: dict[str, list[str]] = {
    "ananta-terminal-hub": [
        "terminal.hub.list",
        "terminal.hub.create",
        "terminal.hub.attach",
        "terminal.hub.read",
        "terminal.hub.write",
        "terminal.hub.kill",
        "terminal.hub_as_worker.create",
        "terminal.hub_as_worker.attach",
    ],
    "ananta-terminal-worker": [
        "terminal.worker.list",
        "terminal.worker.create",
        "terminal.worker.attach",
        "terminal.worker.read",
        "terminal.worker.write",
        "terminal.worker.kill",
    ],
}


def _map_claims_to_auth(claims: dict[str, Any]) -> dict[str, Any]:
    """Map OIDC token claims to Ananta's internal auth context."""
    sub = str(claims.get("sub") or "")
    email = str(claims.get("email") or claims.get("preferred_username") or sub)
    groups: list[str] = []
    raw_groups = claims.get("groups") or claims.get("roles") or []
    if isinstance(raw_groups, list):
        groups = [str(g) for g in raw_groups if g]

    role = "viewer"
    for group in groups:
        mapped = _DEFAULT_CLAIM_ROLE_MAP.get(group)
        if mapped:
            role = mapped
            break

    terminal_permissions: list[str] = []
    for group in groups:
        perms = _DEFAULT_TERMINAL_PERMISSION_MAP.get(group) or []
        terminal_permissions.extend(perms)

    return {
        "sub": sub,
        "username": email,
        "role": role,
        "roles": [role],
        "groups": groups,
        "terminal_permissions": list(set(terminal_permissions)),
        "auth_source": "oidc",
        "email": email,
    }


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
        options={"require": ["sub", "iss", "aud", "exp", "iat"]},
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
    code_challenge = (
        hashlib.sha256(code_verifier.encode()).digest()
        .hex()  # raw bytes base64url would be ideal; hex is deterministic for testing
    )

    session["oidc_state"] = state
    session["oidc_nonce"] = nonce
    session["oidc_code_verifier"] = code_verifier

    redirect_uri = request.host_url.rstrip("/") + "/auth/oidc/callback"
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

    if not state or state != session.get("oidc_state"):
        return api_response(status="error", message="oidc_state_mismatch", code=401)

    if not code:
        return api_response(status="error", message="oidc_code_missing", code=401)

    issuer = settings.terminal_oidc_issuer
    client_id = settings.terminal_oidc_client_id
    audience = settings.terminal_oidc_audience or client_id
    nonce = session.pop("oidc_nonce", None)
    code_verifier = session.pop("oidc_code_verifier", None)
    session.pop("oidc_state", None)

    try:
        discovery = _fetch_oidc_discovery(issuer)
        token_endpoint = discovery.get("token_endpoint")
        if not token_endpoint:
            raise ValueError("oidc_token_endpoint_missing")

        import urllib.parse
        import urllib.request
        redirect_uri = request.host_url.rstrip("/") + "/auth/oidc/callback"
        post_data = urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
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
        LOGGER.info("OIDC login successful for sub=%s role=%s", auth_ctx.get("sub"), auth_ctx.get("role"))
        return jsonify({"ok": True, "auth": auth_ctx})

    except Exception as exc:
        LOGGER.warning("OIDC callback failed: %s", exc)
        return api_response(status="error", message=str(exc), code=401)


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
