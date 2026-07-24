from __future__ import annotations

import asyncio

import pytest

from client_surfaces.operator_tui import hub_loader
from client_surfaces.operator_tui.dashboard_auth import (
    DashboardReauthenticationRequired,
    ResolvingDashboardTokenProvider,
)
from client_surfaces.operator_tui.dashboard_http_adapter import (
    DashboardHubAdapter,
    DashboardPermissionError,
)
from client_surfaces.operator_tui.ops_api_client import OpsApiClient, OpsApiHttpError


def test_password_provider_forces_resolver_refresh() -> None:
    calls: list[bool] = []

    def resolver(endpoint: str, credential: str, *, force_refresh: bool) -> str:
        assert endpoint == "http://hub.test"
        assert credential == "password"
        calls.append(force_refresh)
        return "fresh-token" if force_refresh else "cached-token"

    provider = ResolvingDashboardTokenProvider(
        endpoint="http://hub.test",
        credential="password",
        resolver=resolver,
    )

    assert provider.access_token() == "cached-token"
    assert provider.access_token(force_refresh=True) == "fresh-token"
    assert calls == [False, True]


def test_static_jwt_cannot_claim_to_refresh() -> None:
    provider = ResolvingDashboardTokenProvider(
        endpoint="http://hub.test",
        credential="header.payload.signature",
        resolver=lambda *_args, **_kwargs: "header.payload.signature",
    )

    assert provider.access_token() == "header.payload.signature"
    with pytest.raises(
        DashboardReauthenticationRequired,
        match="dashboard_reauthentication_required",
    ):
        provider.access_token(force_refresh=True)


def test_resolve_token_force_refresh_evicts_password_cache(monkeypatch) -> None:
    logins: list[str] = []
    monkeypatch.setattr(hub_loader, "_jwt_cache", {})
    monkeypatch.setattr(hub_loader, "_load_dotenv_fallback", lambda: {})
    monkeypatch.setenv("ANANTA_USER", "operator")

    def login(base: str, username: str, password: str, timeout: float):
        logins.append(f"{base}:{username}:{password}:{timeout}")
        return f"jwt-{len(logins)}", 4_000_000_000.0

    monkeypatch.setattr(hub_loader, "_login", login)

    first = hub_loader.resolve_token("http://hub.test", "password")
    cached = hub_loader.resolve_token("http://hub.test", "password")
    refreshed = hub_loader.resolve_token(
        "http://hub.test",
        "password",
        force_refresh=True,
    )

    assert (first, cached, refreshed) == ("jwt-1", "jwt-1", "jwt-2")
    assert len(logins) == 2


def test_adapter_retries_401_once_with_fresh_password_token(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []

    def resolver(_endpoint: str, _credential: str, *, force_refresh: bool) -> str:
        return "fresh" if force_refresh else "expired"

    provider = ResolvingDashboardTokenProvider(
        endpoint="http://hub.test",
        credential="password",
        resolver=resolver,
    )

    def request_json(self, method, path, *, payload=None, timeout=5.0):
        calls.append((self._token, payload is None))
        if self._token == "expired":
            raise OpsApiHttpError(
                status_code=401,
                code="token_expired",
                message="token_expired",
            )
        return {"data": {"ok": True}}

    monkeypatch.setattr(OpsApiClient, "request_json", request_json)
    adapter = DashboardHubAdapter(
        endpoint="http://hub.test",
        token="password",
        token_provider=provider,
    )

    result = asyncio.run(adapter._request("GET", "/test"))

    assert result == {"data": {"ok": True}}
    assert calls == [("expired", True), ("fresh", True)]


def test_adapter_static_jwt_fails_closed_after_401(monkeypatch) -> None:
    attempts = 0

    def request_json(self, method, path, *, payload=None, timeout=5.0):
        nonlocal attempts
        attempts += 1
        raise OpsApiHttpError(
            status_code=401,
            code="token_expired",
            message="token_expired",
        )

    monkeypatch.setattr(OpsApiClient, "request_json", request_json)
    adapter = DashboardHubAdapter(
        endpoint="http://hub.test",
        token="header.payload.signature",
    )

    with pytest.raises(DashboardPermissionError) as denied:
        asyncio.run(adapter._request("GET", "/test"))

    assert denied.value.code == "dashboard_reauthentication_required"
    assert denied.value.status_code == 401
    assert attempts == 1
