from __future__ import annotations

import time
from contextlib import nullcontext

import jwt
from werkzeug.security import generate_password_hash

from agent.config import settings
from agent.db_models import RefreshTokenDB, UserDB
from agent.services.oidc_identity_link_service import LinkResult
from agent.services.oidc_settings import OidcConfig
from agent.services.repository_registry import get_repository_registry
from agent.services.user_session_tokens import local_user_tenant_id


def _config() -> OidcConfig:
    return OidcConfig(
        enabled=True,
        issuer_url="https://issuer.example",
        jwks_url="https://issuer.example/jwks",
        audience="ananta-hub",
        client_id="ananta-web",
        jwks_cache_seconds=60,
        allowed_algorithms=("RS256",),
        registration_allowed=False,
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


def test_exchange_issues_tenant_bound_session_for_linked_hub_user(client, monkeypatch):
    from agent.routes import auth_oidc

    linked_user = UserDB(username="oidc-alice", password_hash="unused", role="user")
    links = FakeLinks(resolved_user=linked_user)
    monkeypatch.setattr(auth_oidc, "oidc_is_configured", lambda: True)
    monkeypatch.setattr(auth_oidc, "get_oidc_config", _config)
    monkeypatch.setattr(
        auth_oidc,
        "validate_oidc_token",
        lambda *_args: {"iss": "https://issuer.example", "sub": "kc-alice"},
    )
    monkeypatch.setattr(auth_oidc, "_identity_link_service", lambda: links)
    monkeypatch.setattr(auth_oidc, "log_audit", lambda *_args, **_kwargs: None)

    response = client.post("/auth/oidc/exchange", json={"oidc_access_token": "valid"})

    assert response.status_code == 200
    access_token = response.get_json()["data"]["access_token"]
    claims = jwt.decode(access_token, settings.secret_key, algorithms=["HS256"])
    assert claims["sub"] == linked_user.username
    assert claims["tenant_id"] == local_user_tenant_id(linked_user.username)


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
    assert links.link_calls == [
        {
            "username": "alice",
            "issuer": "https://issuer.example",
            "subject": "kc-alice",
        }
    ]


def _user_token(username: str) -> str:
    return jwt.encode(
        {"sub": username, "role": "user", "exp": time.time() + 60},
        settings.secret_key,
        algorithm="HS256",
    )


def _store_classic_exchange_code(
    auth_oidc,
    *,
    subject: str,
    username: str,
) -> str:
    return auth_oidc._store_frontend_exchange_code(
        {
            "issuer": "https://issuer.example",
            "sub": subject,
            "username": username,
            "email": username,
            "role": "user",
        },
        "/workflows",
    )


def _seed_provider_code_flow(
    client,
    auth_oidc,
    *,
    include_nonce: bool = True,
) -> str:
    state = "provider-state"
    nonce = "provider-nonce"
    code_verifier = "provider-code-verifier"
    browser_session_id = "browser-session-one"
    with client.session_transaction() as oidc_session:
        oidc_session["oidc_state"] = state
        if include_nonce:
            oidc_session["oidc_nonce"] = nonce
        oidc_session["oidc_code_verifier"] = code_verifier
        oidc_session["oidc_browser_session_id"] = browser_session_id
        oidc_session["oidc_redirect_path"] = "/workflows"
    auth_oidc._store_oidc_login_request(
        state=state,
        nonce=nonce,
        code_verifier=code_verifier,
        redirect_path="/workflows",
        browser_session_id=browser_session_id,
    )
    return state


def test_oidc_callback_state_is_bound_to_same_browser_and_single_use(
    client,
    app,
    monkeypatch,
):
    from agent.routes import auth_oidc

    monkeypatch.setattr(settings, "terminal_oidc_enabled", True)
    monkeypatch.setattr(auth_oidc, "log_audit", lambda *_args, **_kwargs: None)
    state = _seed_provider_code_flow(client, auth_oidc)

    other_browser = app.test_client()
    swapped = other_browser.get(
        "/auth/oidc/callback",
        query_string={"state": state, "code": "provider-code"},
    )

    assert swapped.status_code == 401
    assert swapped.get_json()["data"]["reason_code"] == "oidc_session_state_missing"
    assert state in auth_oidc._OIDC_LOGIN_REQUESTS

    consumed = client.get(
        "/auth/oidc/callback",
        query_string={"state": state, "error": "access_denied"},
    )

    assert consumed.status_code == 401
    assert consumed.get_json()["message"] == "oidc_provider_error: access_denied"
    assert state not in auth_oidc._OIDC_LOGIN_REQUESTS

    replay = client.get(
        "/auth/oidc/callback",
        query_string={"state": state, "code": "provider-code"},
    )
    assert replay.status_code == 401
    assert replay.get_json()["data"]["reason_code"] == "oidc_session_state_missing"


def test_oidc_callback_rejects_cross_session_record_swap(client, app, monkeypatch):
    from agent.routes import auth_oidc

    monkeypatch.setattr(settings, "terminal_oidc_enabled", True)
    monkeypatch.setattr(auth_oidc, "log_audit", lambda *_args, **_kwargs: None)
    state = _seed_provider_code_flow(client, auth_oidc)
    swapped_browser = app.test_client()
    with swapped_browser.session_transaction() as oidc_session:
        oidc_session["oidc_state"] = state
        oidc_session["oidc_nonce"] = "provider-nonce"
        oidc_session["oidc_code_verifier"] = "provider-code-verifier"
        oidc_session["oidc_browser_session_id"] = "different-browser-session"

    response = swapped_browser.get(
        "/auth/oidc/callback",
        query_string={"state": state, "code": "provider-code"},
    )

    assert response.status_code == 401
    assert response.get_json()["data"]["reason_code"] == "oidc_browser_session_mismatch"
    assert state not in auth_oidc._OIDC_LOGIN_REQUESTS


def test_direct_code_exchange_requires_state_and_nonce(client, monkeypatch):
    from agent.routes import auth_oidc

    monkeypatch.setattr(settings, "terminal_oidc_enabled", True)
    monkeypatch.setattr(settings, "terminal_oidc_issuer", "https://issuer.example")
    monkeypatch.setattr(settings, "terminal_oidc_client_id", "ananta-web")
    monkeypatch.setattr(auth_oidc, "log_audit", lambda *_args, **_kwargs: None)

    missing_state = client.post(
        "/auth/oidc/exchange",
        json={"code": "provider-code"},
    )
    assert missing_state.status_code == 401
    assert missing_state.get_json()["data"]["reason_code"] == "oidc_state_missing"

    state = _seed_provider_code_flow(client, auth_oidc, include_nonce=False)
    missing_nonce = client.post(
        "/auth/oidc/exchange",
        json={"code": "provider-code", "state": state},
    )
    assert missing_nonce.status_code == 401
    assert missing_nonce.get_json()["data"]["reason_code"] == "oidc_nonce_missing"
    assert state not in auth_oidc._OIDC_LOGIN_REQUESTS
    with client.session_transaction() as oidc_session:
        assert "oidc_browser_session_id" not in oidc_session


def test_oidc_provider_failures_never_expose_sensitive_exception_details(
    client,
    monkeypatch,
    caplog,
    capsys,
):
    from agent.routes import auth_oidc

    sensitive_marker = "provider-secret-token-and-response-body"

    def fail_discovery(_issuer):
        raise RuntimeError(sensitive_marker)

    monkeypatch.setattr(settings, "terminal_oidc_enabled", True)
    monkeypatch.setattr(settings, "terminal_oidc_issuer", "https://issuer.example")
    monkeypatch.setattr(settings, "terminal_oidc_client_id", "ananta-web")
    monkeypatch.setattr(auth_oidc, "_fetch_oidc_discovery", fail_discovery)
    monkeypatch.setattr(auth_oidc, "log_audit", lambda *_args, **_kwargs: None)

    login = client.get("/auth/oidc/login")

    callback_state = _seed_provider_code_flow(client, auth_oidc)
    callback = client.get(
        "/auth/oidc/callback",
        query_string={"state": callback_state, "code": "provider-code"},
    )

    exchange_state = _seed_provider_code_flow(client, auth_oidc)
    exchange = client.post(
        "/auth/oidc/exchange",
        json={"state": exchange_state, "code": "provider-code"},
    )

    assert login.status_code == 503
    assert login.get_json()["data"]["reason_code"] == "oidc_discovery_failed"
    assert callback.status_code == 401
    assert callback.get_json()["data"]["reason_code"] == "oidc_callback_failed"
    assert exchange.status_code == 401
    assert exchange.get_json()["data"]["reason_code"] == "oidc_code_exchange_failed"
    captured = capsys.readouterr()
    rendered_responses = repr(
        [login.get_json(), callback.get_json(), exchange.get_json()]
    )
    assert sensitive_marker not in rendered_responses
    assert sensitive_marker not in caplog.text
    assert sensitive_marker not in captured.out
    assert sensitive_marker not in captured.err


def test_link_rejects_noncanonical_legacy_hub_subject_before_repository_access(
    client,
    monkeypatch,
):
    from agent.routes import auth_oidc

    links = FakeLinks()
    audit_events: list[tuple[str, dict]] = []
    monkeypatch.setattr(auth_oidc, "oidc_is_configured", lambda: True)
    monkeypatch.setattr(auth_oidc, "get_oidc_config", _config)
    monkeypatch.setattr(auth_oidc, "_identity_link_service", lambda: links)
    monkeypatch.setattr(
        auth_oidc,
        "log_audit",
        lambda event, payload: audit_events.append((event, payload)),
    )

    response = client.get(
        "/auth/oidc/link",
        headers={"Authorization": f"Bearer {_user_token(' legacy-user ')}"},
    )

    assert response.status_code == 409
    assert response.get_json()["message"] == "user_session_username_not_canonical"
    assert audit_events == [
        (
            "oidc_identity_rejected",
            {
                "endpoint": "auth_oidc.oidc_identity_link",
                "phase": "hub_account_link",
                "reason_code": "user_session_username_not_canonical",
            },
        )
    ]


def test_linked_exchange_rejects_noncanonical_oidc_subject_and_audits_reason(
    client,
    monkeypatch,
):
    from agent.routes import auth_oidc

    audit_events: list[tuple[str, dict]] = []
    monkeypatch.setattr(auth_oidc, "oidc_is_configured", lambda: True)
    monkeypatch.setattr(auth_oidc, "get_oidc_config", _config)
    monkeypatch.setattr(
        auth_oidc,
        "validate_oidc_token",
        lambda *_args: {"iss": "https://issuer.example", "sub": " kc-user "},
    )
    monkeypatch.setattr(
        auth_oidc,
        "log_audit",
        lambda event, payload: audit_events.append((event, payload)),
    )

    response = client.post(
        "/auth/oidc/exchange",
        json={"oidc_access_token": "valid"},
    )

    assert response.status_code == 409
    assert response.get_json()["message"] == "oidc_subject_not_canonical"
    assert response.get_json()["data"]["reason_code"] == "oidc_subject_not_canonical"
    assert audit_events[0][0] == "oidc_identity_rejected"
    assert audit_events[0][1]["reason_code"] == "oidc_subject_not_canonical"


def test_classic_oidc_provisioning_binds_subject_and_rejects_duplicate_email(
    client,
    monkeypatch,
):
    from agent.routes import auth_oidc

    monkeypatch.setattr(settings, "terminal_oidc_enabled", True)
    monkeypatch.setattr(auth_oidc, "log_audit", lambda *_args, **_kwargs: None)
    first_code = _store_classic_exchange_code(
        auth_oidc,
        subject="subject-one",
        username="shared@example.test",
    )

    first = client.post("/auth/oidc/exchange", json={"code": first_code})

    assert first.status_code == 200
    first_claims = jwt.decode(
        first.get_json()["data"]["access_token"],
        settings.secret_key,
        algorithms=["HS256"],
    )
    assert first_claims["tenant_id"] == "shared@example.test"

    second_code = _store_classic_exchange_code(
        auth_oidc,
        subject="subject-two",
        username="shared@example.test",
    )
    second = client.post("/auth/oidc/exchange", json={"code": second_code})

    assert second.status_code == 409
    assert second.get_json()["message"] == "oidc_local_account_requires_explicit_link"


def test_classic_oidc_bound_subject_keeps_tenant_when_email_changes(client, monkeypatch):
    from agent.routes import auth_oidc

    monkeypatch.setattr(settings, "terminal_oidc_enabled", True)
    monkeypatch.setattr(auth_oidc, "log_audit", lambda *_args, **_kwargs: None)
    initial_code = _store_classic_exchange_code(
        auth_oidc,
        subject="stable-subject",
        username="original@example.test",
    )
    assert client.post("/auth/oidc/exchange", json={"code": initial_code}).status_code == 200

    changed_code = _store_classic_exchange_code(
        auth_oidc,
        subject="stable-subject",
        username="changed@example.test",
    )
    changed = client.post("/auth/oidc/exchange", json={"code": changed_code})

    assert changed.status_code == 200
    claims = jwt.decode(
        changed.get_json()["data"]["access_token"],
        settings.secret_key,
        algorithms=["HS256"],
    )
    assert claims["sub"] == "original@example.test"
    assert claims["tenant_id"] == "original@example.test"


def test_classic_code_exchange_returns_409_for_identity_rejection(client, monkeypatch):
    from agent.routes import auth_oidc

    class TokenResponse:
        def read(self):
            return b'{"id_token":"signed-id-token","access_token":"provider-token"}'

    monkeypatch.setattr(settings, "terminal_oidc_enabled", True)
    monkeypatch.setattr(settings, "terminal_oidc_issuer", "https://issuer.example")
    monkeypatch.setattr(settings, "terminal_oidc_client_id", "ananta-web")
    monkeypatch.setattr(
        auth_oidc,
        "_fetch_oidc_discovery",
        lambda _issuer: {"token_endpoint": "https://issuer.example/token"},
    )
    monkeypatch.setattr(
        auth_oidc,
        "_validate_id_token",
        lambda *_args, **_kwargs: {
            "iss": "https://issuer.example",
            "sub": " invalid-subject ",
            "email": "valid@example.test",
        },
    )
    monkeypatch.setattr(auth_oidc, "log_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: nullcontext(TokenResponse()),
    )
    state = _seed_provider_code_flow(client, auth_oidc)

    response = client.post(
        "/auth/oidc/exchange",
        json={"code": "provider-code", "state": state},
    )

    assert response.status_code == 409
    assert response.get_json()["message"] == "oidc_subject_not_canonical"


def test_classic_code_exchange_provisioning_failure_is_503_without_user_session(
    client,
    monkeypatch,
):
    from agent.routes import auth_oidc
    from agent.services.oidc_identity_link_service import (
        OidcAccountProvisioningUnavailableError,
    )

    class TokenResponse:
        def read(self):
            return b'{"id_token":"signed-id-token","access_token":"provider-token"}'

    def fail_provisioning(_auth_ctx):
        raise OidcAccountProvisioningUnavailableError()

    monkeypatch.setattr(settings, "terminal_oidc_enabled", True)
    monkeypatch.setattr(settings, "terminal_oidc_issuer", "https://issuer.example")
    monkeypatch.setattr(settings, "terminal_oidc_client_id", "ananta-web")
    monkeypatch.setattr(
        auth_oidc,
        "_fetch_oidc_discovery",
        lambda _issuer: {"token_endpoint": "https://issuer.example/token"},
    )
    monkeypatch.setattr(
        auth_oidc,
        "_validate_id_token",
        lambda *_args, **_kwargs: {
            "iss": "https://issuer.example",
            "sub": "valid-subject",
            "email": "valid@example.test",
        },
    )
    monkeypatch.setattr(auth_oidc, "_ensure_local_user_account", fail_provisioning)
    monkeypatch.setattr(auth_oidc, "log_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: nullcontext(TokenResponse()),
    )
    state = _seed_provider_code_flow(client, auth_oidc)

    response = client.post(
        "/auth/oidc/exchange",
        json={"code": "provider-code", "state": state},
    )

    assert response.status_code == 503
    assert response.get_json()["data"]["reason_code"] == (
        "oidc_identity_provisioning_unavailable"
    )
    with client.session_transaction() as oidc_session:
        assert "user" not in oidc_session


def test_refresh_rejection_keeps_legacy_refresh_token(client):
    repos = get_repository_registry()
    repos.user_repo.save(
        UserDB(
            username=" legacy-refresh ",
            password_hash=generate_password_hash("Password123!"),
            role="user",
        )
    )
    repos.refresh_token_repo.save(
        RefreshTokenDB(
            token="legacy-refresh-token",
            username=" legacy-refresh ",
            expires_at=time.time() + 60,
        )
    )

    response = client.post(
        "/refresh-token",
        json={"refresh_token": "legacy-refresh-token"},
    )

    assert response.status_code == 409
    assert repos.refresh_token_repo.get_by_token("legacy-refresh-token") is not None


def test_mfa_setup_rejection_does_not_persist_secret(client):
    repos = get_repository_registry()
    username = " legacy-mfa "
    repos.user_repo.save(
        UserDB(
            username=username,
            password_hash=generate_password_hash("Password123!"),
            role="user",
            mfa_secret=None,
            mfa_enabled=False,
        )
    )

    response = client.post(
        "/mfa/setup",
        headers={"Authorization": f"Bearer {_user_token(username)}"},
    )

    assert response.status_code == 409
    persisted = repos.user_repo.get_by_username(username)
    assert persisted is not None
    assert persisted.mfa_secret is None
    assert persisted.mfa_enabled is False
