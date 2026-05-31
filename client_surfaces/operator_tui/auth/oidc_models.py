"""Data models for the Carbonyl OIDC auth subsystem.

These models are separate from the Device Flow (oidc_device_flow.py) and
support authorization_code_pkce flows with loopback callback.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OidcProviderConfig:
    """Configuration for an OIDC provider."""
    provider_id: str
    issuer: str
    client_id: str
    flow: str  # "authorization_code_pkce"
    redirect_mode: str  # "loopback"
    allowed_redirect_hosts: list[str] = field(default_factory=list)


@dataclass
class OidcAuthRequest:
    """In-flight OIDC authorization request.

    Contains all ephemeral PKCE/state/nonce values for one login attempt.
    Never persisted to disk or logged with token values.
    """
    provider_id: str
    state: str
    nonce: str
    pkce_verifier: str
    pkce_challenge: str
    redirect_uri: str
    authorization_url: str
    created_at: float
    expires_at: float

    def __repr__(self) -> str:
        # Never expose verifier in repr
        return (
            f"OidcAuthRequest(provider_id={self.provider_id!r}, "
            f"state={self.state!r}, "
            f"redirect_uri={self.redirect_uri!r}, "
            f"pkce_verifier=<redacted>, "
            f"pkce_challenge={self.pkce_challenge!r})"
        )


@dataclass
class OidcAuthResult:
    """Result of a completed OIDC authorization flow.

    Token fields are never logged or included in str/repr output.
    """
    ok: bool
    access_token: str = ""
    id_token: str = ""
    refresh_token: str = ""
    error: str = ""
    provider_id: str = ""
    subject: str = ""
    username: str = ""

    def __repr__(self) -> str:
        # Never expose raw token values in repr
        token_present = bool(self.access_token)
        return (
            f"OidcAuthResult(ok={self.ok!r}, "
            f"provider_id={self.provider_id!r}, "
            f"subject={self.subject!r}, "
            f"username={self.username!r}, "
            f"access_token={'<present>' if token_present else '<absent>'}, "
            f"error={self.error!r})"
        )

    def __str__(self) -> str:
        return self.__repr__()
