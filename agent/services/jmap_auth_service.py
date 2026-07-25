from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Mapping, Protocol

from agent.services.mail_contract_service import MailAccountV2
from agent.services.mail_provider_ports import MailAuthMaterial, MailProviderResult


@dataclass(frozen=True, slots=True, repr=False)
class JmapOAuthAccessToken:
    access_token: str
    token_type: str = "Bearer"
    expires_at: str = ""

    def __repr__(self) -> str:
        return "JmapOAuthAccessToken(access_token='[REDACTED]', token_type='Bearer')"


class JmapOAuthTokenAdapter(Protocol):
    """OAuth infrastructure adapter. The auth service never persists returned tokens."""

    def acquire_access_token(
        self,
        *,
        account_id: str,
        username: str,
        credential: str,
        force_refresh: bool,
    ) -> MailProviderResult[JmapOAuthAccessToken]:
        ...


class JmapAuthorizationProvider(Protocol):
    @property
    def supports_rotation(self) -> bool:
        ...

    def headers(
        self,
        *,
        force_refresh: bool = False,
    ) -> MailProviderResult[Mapping[str, str]]:
        ...


class StaticJmapAuthorizationProvider:
    def __init__(self, *, headers: Mapping[str, str]) -> None:
        self._headers = dict(headers)

    @property
    def supports_rotation(self) -> bool:
        return False

    def headers(
        self,
        *,
        force_refresh: bool = False,
    ) -> MailProviderResult[Mapping[str, str]]:
        del force_refresh
        return MailProviderResult(ok=True, reason_code="ok", value=dict(self._headers))

    def __repr__(self) -> str:
        return "StaticJmapAuthorizationProvider(headers='[REDACTED]')"


class _OAuthAuthorizationProvider:
    __slots__ = ("_account_id", "_username", "_credential", "_adapter")

    def __init__(
        self,
        *,
        account_id: str,
        username: str,
        credential: str,
        adapter: JmapOAuthTokenAdapter,
    ) -> None:
        self._account_id = account_id
        self._username = username
        self._credential = credential
        self._adapter = adapter

    @property
    def supports_rotation(self) -> bool:
        return True

    def headers(
        self,
        *,
        force_refresh: bool = False,
    ) -> MailProviderResult[Mapping[str, str]]:
        try:
            result = self._adapter.acquire_access_token(
                account_id=self._account_id,
                username=self._username,
                credential=self._credential,
                force_refresh=bool(force_refresh),
            )
        except Exception:
            return MailProviderResult(
                ok=False,
                reason_code="jmap_oauth_adapter_unavailable",
                retryable=True,
            )
        if not result.ok or result.value is None:
            return MailProviderResult(
                ok=False,
                reason_code=result.reason_code,
                retryable=result.retryable,
                retry_after_ms=result.retry_after_ms,
                details=result.details,
            )
        token = result.value
        value = str(token.access_token or "")
        if (
            str(token.token_type or "").lower() != "bearer"
            or not value
            or len(value) > 16 * 1024
            or "\r" in value
            or "\n" in value
        ):
            return MailProviderResult(ok=False, reason_code="jmap_oauth_token_invalid")
        return MailProviderResult(
            ok=True,
            reason_code="ok",
            value={"Authorization": f"Bearer {value}", "Accept": "application/json"},
        )

    def __repr__(self) -> str:
        return "_OAuthAuthorizationProvider(credentials='[REDACTED]')"


class JmapAuthService:
    _OAUTH_MODES = frozenset({"oauth2", "oauth2_bearer"})
    _BEARER_MODES = frozenset({"bearer"})
    _BASIC_MODES = frozenset({"basic", "app_password", "password_app_token"})

    def __init__(self, *, oauth_adapter: JmapOAuthTokenAdapter | None = None) -> None:
        self._oauth_adapter = oauth_adapter

    def bind(
        self,
        *,
        account: MailAccountV2,
        auth: MailAuthMaterial,
    ) -> MailProviderResult[JmapAuthorizationProvider]:
        config = dict(account.provider_config or {})
        mode = str(config.get("auth_mode") or "bearer").strip().lower()
        credential = str(auth.credential or "")
        username = str(auth.username or "")
        if not credential:
            return MailProviderResult(ok=False, reason_code="jmap_credential_required")
        if mode in self._OAUTH_MODES:
            if self._oauth_adapter is None:
                return MailProviderResult(ok=False, reason_code="jmap_oauth_adapter_required")
            provider: JmapAuthorizationProvider = _OAuthAuthorizationProvider(
                account_id=account.account_id,
                username=username,
                credential=credential,
                adapter=self._oauth_adapter,
            )
        elif mode in self._BEARER_MODES:
            if "\r" in credential or "\n" in credential:
                return MailProviderResult(ok=False, reason_code="jmap_credential_invalid")
            provider = StaticJmapAuthorizationProvider(
                headers={"Authorization": f"Bearer {credential}", "Accept": "application/json"}
            )
        elif mode in self._BASIC_MODES:
            if not username or ":" in username or "\r" in username or "\n" in username:
                return MailProviderResult(ok=False, reason_code="jmap_basic_username_invalid")
            encoded = base64.b64encode(f"{username}:{credential}".encode("utf-8")).decode("ascii")
            provider = StaticJmapAuthorizationProvider(
                headers={"Authorization": f"Basic {encoded}", "Accept": "application/json"}
            )
        else:
            return MailProviderResult(ok=False, reason_code="jmap_auth_mode_unsupported")
        return MailProviderResult(ok=True, reason_code="ok", value=provider)

    def authorization_headers(
        self,
        *,
        account: MailAccountV2,
        auth: MailAuthMaterial,
    ) -> MailProviderResult[Mapping[str, str]]:
        bound = self.bind(account=account, auth=auth)
        if not bound.ok or bound.value is None:
            return MailProviderResult(
                ok=False,
                reason_code=bound.reason_code,
                retryable=bound.retryable,
                retry_after_ms=bound.retry_after_ms,
                details=bound.details,
            )
        return bound.value.headers()

    @staticmethod
    def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
        return {
            str(key): "[REDACTED_SECRET]" if str(key).lower() == "authorization" else str(value)
            for key, value in headers.items()
        }


__all__ = [
    "JmapAuthorizationProvider",
    "JmapAuthService",
    "JmapOAuthAccessToken",
    "JmapOAuthTokenAdapter",
    "StaticJmapAuthorizationProvider",
]
