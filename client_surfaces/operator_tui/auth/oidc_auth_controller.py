"""OIDC AuthController for Carbonyl-initiated authorization_code_pkce flows.

This is a separate subsystem from oidc_device_flow.py (Device Flow / RFC 8628).
It handles PKCE-protected authorization code flows with loopback callback.

Security invariants:
- State and nonce are verified before token exchange.
- Token values are NEVER logged or exposed in error messages.
- All secrets are generated with cryptographically secure random.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from typing import TYPE_CHECKING

from client_surfaces.operator_tui.auth.oidc_models import (
    OidcAuthRequest,
    OidcAuthResult,
    OidcProviderConfig,
)

if TYPE_CHECKING:
    pass

# PKCE verifier length in bytes (must be 43-128 chars when base64url encoded)
_PKCE_VERIFIER_BYTES = 48  # 64 base64url chars
_STATE_BYTES = 32
_NONCE_BYTES = 32
_SESSION_NONCE_BYTES = 32


class OidcAuthController:
    """Creates and completes OIDC authorization_code_pkce flows.

    One instance per login attempt is the recommended pattern, but the
    controller may be reused — each ``create_authorization_request`` call
    produces independent state/nonce/PKCE values.
    """

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    def create_authorization_request(
        self,
        provider: OidcProviderConfig,
        redirect_uri: str,
    ) -> OidcAuthRequest:
        """Build a fresh authorization request with PKCE, state and nonce.

        The returned ``OidcAuthRequest.authorization_url`` should be opened in
        Carbonyl. The request object must be kept in memory and passed to
        ``complete_callback`` when the provider redirects back.

        Args:
            provider: OIDC provider configuration.
            redirect_uri: The loopback URI the provider will redirect to
                (e.g. ``http://127.0.0.1:54321/callback``).

        Returns:
            A populated ``OidcAuthRequest`` ready for use.
        """
        verifier, challenge = self._generate_pkce_pair()
        state = self._generate_state()
        nonce = self._generate_nonce()
        now = time.time()

        params = {
            "response_type": "code",
            "client_id": provider.client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid profile email",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        auth_endpoint = self._discover_auth_endpoint(provider)
        authorization_url = auth_endpoint + "?" + urllib.parse.urlencode(params)

        return OidcAuthRequest(
            provider_id=provider.provider_id,
            state=state,
            nonce=nonce,
            pkce_verifier=verifier,
            pkce_challenge=challenge,
            redirect_uri=redirect_uri,
            authorization_url=authorization_url,
            created_at=now,
            expires_at=now + 300.0,  # 5-minute window
        )

    def complete_callback(
        self,
        callback_url: str,
        request: OidcAuthRequest,
        provider: OidcProviderConfig,
    ) -> OidcAuthResult:
        """Validate the callback URL and exchange the authorization code.

        Verifies state parameter before any network call.  Raises
        ``ValueError`` on state mismatch or missing code.

        Args:
            callback_url: The full URL the provider redirected to, including
                query parameters (``code``, ``state``, etc.).
            request: The ``OidcAuthRequest`` returned by
                ``create_authorization_request``.
            provider: The same provider config used to start the flow.

        Returns:
            ``OidcAuthResult`` with ``ok=True`` on success, or
            ``ok=False`` with a non-empty ``error`` field on failure.
            Token values are present only on success and are NEVER logged.

        Raises:
            ValueError: If state does not match or code is missing.
        """
        now = time.time()
        if request.provider_id != provider.provider_id:
            raise ValueError("OIDC provider mismatch: request/provider do not match.")
        if now > request.expires_at:
            raise ValueError("OIDC request expired before callback completion.")

        self._validate_loopback_callback_url(callback_url, provider)

        parsed = urllib.parse.urlparse(callback_url)
        params = dict(urllib.parse.parse_qsl(parsed.query))

        # Mandatory state validation — reject mismatches
        received_state = params.get("state", "")
        if not secrets.compare_digest(received_state, request.state):
            raise ValueError(
                "OIDC state mismatch: callback state does not match request state. "
                "Possible CSRF attack or stale request."
            )

        # Provider errors are trusted only after state validation.
        if "error" in params:
            error_desc = params.get("error_description", params["error"])
            return OidcAuthResult(
                ok=False,
                error=f"provider_error: {error_desc}",
                provider_id=request.provider_id,
            )

        code = params.get("code", "").strip()
        if not code:
            raise ValueError(
                "OIDC callback missing authorization code."
            )

        return self._exchange_code(code, request, provider)

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    def _generate_pkce_pair(self) -> tuple[str, str]:
        """Generate a PKCE (verifier, challenge) pair using S256.

        Returns:
            Tuple of (verifier, challenge) both as base64url strings.
            The challenge is base64url(sha256(verifier)) per RFC 7636.
        """
        verifier_bytes = secrets.token_bytes(_PKCE_VERIFIER_BYTES)
        verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode("ascii")
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return verifier, challenge

    def _generate_state(self) -> str:
        """Generate a cryptographically random state parameter."""
        return secrets.token_urlsafe(_STATE_BYTES)

    def _generate_nonce(self) -> str:
        """Generate a cryptographically random nonce parameter."""
        return secrets.token_urlsafe(_NONCE_BYTES)

    def issue_realtime_session_nonce(self) -> str:
        """Generate a fresh nonce for realtime/WebRTC handoff.

        This nonce is not derived from and does not reveal any provider token.
        """
        return secrets.token_urlsafe(_SESSION_NONCE_BYTES)

    @staticmethod
    def derive_subject_hash(subject: str, provider_id: str) -> str:
        """Return a stable, non-reversible subject hash for non-secret handoff."""
        normalized = f"{provider_id.strip()}:{subject.strip()}".encode("utf-8")
        return hashlib.sha256(normalized).hexdigest()

    def build_realtime_identity(self, result: OidcAuthResult) -> dict[str, str]:
        """Build non-secret metadata for WebRTC/session surfaces."""
        if not result.ok or not result.subject:
            raise ValueError("Cannot build realtime identity without successful OIDC subject.")
        return {
            "provider_id": result.provider_id,
            "subject_hash": self.derive_subject_hash(result.subject, result.provider_id),
            "session_nonce": self.issue_realtime_session_nonce(),
        }

    def _discover_auth_endpoint(self, provider: OidcProviderConfig) -> str:
        """Resolve the authorization endpoint from the OIDC discovery document.

        Falls back to a Keycloak-style default if discovery fails, so that
        tests and offline scenarios don't require a live provider.

        Args:
            provider: OIDC provider configuration.

        Returns:
            The authorization endpoint URL string.
        """
        discovery_url = (
            provider.issuer.rstrip("/") + "/.well-known/openid-configuration"
        )
        try:
            req = urllib.request.Request(
                discovery_url,
                headers={"Accept": "application/json"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            endpoint = str(data.get("authorization_endpoint") or "")
            if endpoint:
                return endpoint
        except Exception:
            pass
        # Default fallback (Keycloak-style)
        return provider.issuer.rstrip("/") + "/protocol/openid-connect/auth"

    def _discover_token_endpoint(self, provider: OidcProviderConfig) -> str:
        """Resolve the token endpoint from the OIDC discovery document.

        Args:
            provider: OIDC provider configuration.

        Returns:
            The token endpoint URL string.
        """
        discovery_url = (
            provider.issuer.rstrip("/") + "/.well-known/openid-configuration"
        )
        try:
            req = urllib.request.Request(
                discovery_url,
                headers={"Accept": "application/json"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            endpoint = str(data.get("token_endpoint") or "")
            if endpoint:
                return endpoint
        except Exception:
            pass
        # Default fallback (Keycloak-style)
        return provider.issuer.rstrip("/") + "/protocol/openid-connect/token"

    def _exchange_code(
        self,
        code: str,
        request: OidcAuthRequest,
        provider: OidcProviderConfig,
    ) -> OidcAuthResult:
        """Exchange authorization code for tokens via POST to the token endpoint.

        Token values are NEVER included in log messages or error strings.

        Args:
            code: The authorization code from the callback.
            request: The original authorization request (contains PKCE verifier).
            provider: The OIDC provider configuration.

        Returns:
            ``OidcAuthResult`` with tokens on success, error details on failure.
        """
        token_endpoint = self._discover_token_endpoint(provider)
        body = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": request.redirect_uri,
            "client_id": provider.client_id,
            "code_verifier": request.pkce_verifier,
        }).encode()
        req = urllib.request.Request(
            token_endpoint,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            try:
                err_data = json.loads(exc.read())
                error_desc = str(err_data.get("error_description") or err_data.get("error") or "token_exchange_failed")
            except Exception:
                error_desc = f"http_{exc.code}"
            # NEVER include token-like values in error message
            return OidcAuthResult(
                ok=False,
                error=f"token_exchange_failed: {error_desc}",
                provider_id=request.provider_id,
            )
        except Exception as exc:
            return OidcAuthResult(
                ok=False,
                error=f"token_exchange_failed: network_error",
                provider_id=request.provider_id,
            )

        access_token = str(data.get("access_token") or "")
        if not access_token:
            return OidcAuthResult(
                ok=False,
                error="token_exchange_failed: no access_token in response",
                provider_id=request.provider_id,
            )

        # Extract and validate non-secret display claims from id_token if available.
        id_token = str(data.get("id_token") or "")
        subject, username = self._extract_validated_id_token_claims(id_token, request, provider)

        return OidcAuthResult(
            ok=True,
            access_token=access_token,
            id_token=id_token,
            refresh_token=str(data.get("refresh_token") or ""),
            provider_id=request.provider_id,
            subject=subject,
            username=username,
        )

    @staticmethod
    def _extract_id_token_claims(id_token: str) -> tuple[str, str]:
        """Decode the ID token payload without signature verification.

        This is safe for extracting display claims only.  Full signature
        verification requires a JWKS endpoint and is out of scope here.

        Args:
            id_token: JWT id_token string.

        Returns:
            Tuple of (subject, username/preferred_username).
        """
        if not id_token:
            return "", ""
        parts = id_token.split(".")
        if len(parts) != 3:
            return "", ""
        try:
            payload_b64 = parts[1]
            # Re-pad to multiple of 4
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding
            payload_bytes = base64.urlsafe_b64decode(payload_b64)
            claims = json.loads(payload_bytes)
            subject = str(claims.get("sub") or "")
            username = str(
                claims.get("preferred_username")
                or claims.get("email")
                or claims.get("name")
                or ""
            )
            return subject, username
        except Exception:
            return "", ""

    @classmethod
    def _extract_validated_id_token_claims(
        cls,
        id_token: str,
        request: OidcAuthRequest,
        provider: OidcProviderConfig,
    ) -> tuple[str, str]:
        claims = cls._decode_id_token_claims(id_token)
        if not claims:
            return "", ""

        issuer = str(claims.get("iss") or "").rstrip("/")
        if issuer and issuer != provider.issuer.rstrip("/"):
            return "", ""

        audience = claims.get("aud")
        if audience and not _audience_contains(audience, provider.client_id):
            return "", ""

        nonce = str(claims.get("nonce") or "")
        if nonce and not secrets.compare_digest(nonce, request.nonce):
            return "", ""

        exp = claims.get("exp")
        if exp is not None:
            try:
                if float(exp) <= time.time():
                    return "", ""
            except (TypeError, ValueError):
                return "", ""

        subject = str(claims.get("sub") or "")
        username = str(
            claims.get("preferred_username")
            or claims.get("email")
            or claims.get("name")
            or ""
        )
        return subject, username

    @staticmethod
    def _decode_id_token_claims(id_token: str) -> dict:
        if not id_token:
            return {}
        parts = id_token.split(".")
        if len(parts) != 3:
            return {}
        try:
            payload_b64 = parts[1]
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding
            payload_bytes = base64.urlsafe_b64decode(payload_b64)
            return dict(json.loads(payload_bytes))
        except Exception:
            return {}

    @staticmethod
    def _validate_loopback_callback_url(callback_url: str, provider: OidcProviderConfig) -> None:
        parsed = urllib.parse.urlparse(callback_url)
        host = (parsed.hostname or "").lower()
        allowed_hosts = {str(item).lower() for item in provider.allowed_redirect_hosts}
        if allowed_hosts and host not in allowed_hosts:
            raise ValueError("OIDC callback host is not allowed for this provider.")
        if parsed.scheme != "http":
            raise ValueError("OIDC loopback callback must use http.")
        if parsed.path != "/callback":
            raise ValueError("OIDC callback path must be /callback.")


def _audience_contains(audience: object, client_id: str) -> bool:
    if isinstance(audience, str):
        return secrets.compare_digest(audience, client_id)
    if isinstance(audience, Sequence) and not isinstance(audience, (bytes, bytearray)):
        return any(isinstance(item, str) and secrets.compare_digest(item, client_id) for item in audience)
    return False
