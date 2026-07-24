from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from client_surfaces.operator_tui.hub_loader import resolve_token


class DashboardReauthenticationRequired(PermissionError):
    """Signals that a credential cannot renew an expired access token."""

    code = "dashboard_reauthentication_required"

    def __init__(self) -> None:
        super().__init__(self.code)


class DashboardTokenProvider(Protocol):
    """Small auth port shared by snapshot and live-event transports."""

    def access_token(self, *, force_refresh: bool = False) -> str:
        ...


TokenResolver = Callable[..., str]


class ResolvingDashboardTokenProvider:
    """Resolves password credentials while treating static JWTs as non-renewable."""

    def __init__(
        self,
        *,
        endpoint: str,
        credential: str,
        resolver: TokenResolver = resolve_token,
    ) -> None:
        self._endpoint = str(endpoint or "").strip().rstrip("/")
        self._credential = str(credential or "").strip()
        self._resolver = resolver
        self._static_jwt = self._credential.count(".") >= 2

    def access_token(self, *, force_refresh: bool = False) -> str:
        if force_refresh and (not self._credential or self._static_jwt):
            raise DashboardReauthenticationRequired()
        return str(
            self._resolver(
                self._endpoint,
                self._credential,
                force_refresh=force_refresh,
            )
            or ""
        ).strip()
