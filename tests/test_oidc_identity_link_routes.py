from __future__ import annotations

import time

import jwt

from agent.config import settings
from agent.db_models import UserDB
from agent.services.oidc_identity_link_service import LinkResult
from agent.services.oidc_settings import OidcConfig


def _config() -> OidcConfig:
    return OidcConfig(
        enabled=True,
        issuer_url="https://issuer.example",
        jwks_url="https://issuer.example/jwks",
        audience="ananta-hub",
        client_id="ananta-web",
        jwks_cache_seconds=60,
        allowed_algorithms=("RS256",),
    )


class FakeLinks:
    def __init__(self, resolved_user: UserDB | None = None) -> None:
        self.resolved_user = resolved_user
        self.link_calls: list[dict[str, str]] = []

    def resolve(self, **_kwargs):
        return self.resolved_user

    def link(self, **kwargs):
        self.link_calls.append(kwargs)
        return LinkResult(**kwargs)

    def status(self, **_kwargs):
        return None

    def unlink(self, **_kwargs):
        return False


def test_exchange_rejects_unlinked_oidc_identity(client, monkeypatch):
    from agent.routes import auth_oidc

    links = FakeLinks()
    monkeypatch.setattr(auth_oidc, "oidc_is_configured", lambda: True)
    monkeypatch.setattr(auth_oidc, "get_oidc_config", _config)
    monkeypatch.setattr(
        auth_oidc,
        "validate_oidc_token",
        lambda *_args: {"iss": "https://issuer.example", "sub": "kc-user"},
    )
    monkeypatch.setattr(auth_oidc, "_identity_link_service", lambda: links)

    response = client.post("/auth/oidc/exchange", json={"oidc_access_token": "valid"})

    assert response.status_code == 409
    assert response.get_json()["message"] == "oidc_identity_not_linked"


def test_link_requires_hub_session_and_records_explicit_mapping(client, monkeypatch):
    from agent.routes import auth_oidc

    links = FakeLinks()
    monkeypatch.setattr(auth_oidc, "oidc_is_configured", lambda: True)
    monkeypatch.setattr(auth_oidc, "get_oidc_config", _config)
    monkeypatch.setattr(
        auth_oidc,
        "validate_oidc_token",
        lambda *_args: {"iss": "https://issuer.example", "sub": "kc-alice"},
    )
    monkeypatch.setattr(auth_oidc, "_identity_link_service", lambda: links)
    monkeypatch.setattr(auth_oidc, "log_audit", lambda *_args, **_kwargs: None)
    hub_token = jwt.encode(
        {"sub": "alice", "exp": time.time() + 60},
        settings.secret_key,
        algorithm="HS256",
    )

    unauthenticated = client.post("/auth/oidc/link", json={"oidc_access_token": "valid"})
    linked = client.post(
        "/auth/oidc/link",
        json={"oidc_access_token": "valid"},
        headers={"Authorization": f"Bearer {hub_token}"},
    )

    assert unauthenticated.status_code == 401
    assert linked.status_code == 200
    assert links.link_calls == [{
        "username": "alice",
        "issuer": "https://issuer.example",
        "subject": "kc-alice",
    }]
