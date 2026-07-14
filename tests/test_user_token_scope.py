from __future__ import annotations

import time

import jwt
from flask import Flask

from agent.config import settings
from agent.routes.snakes_state import _optional_user_auth
from agent.services.user_token_scope import (
    control_center_stream_identity_is_bound,
    snake_events_stream_identity_is_bound,
    token_scope_allows_request,
)
from agent.ws_terminal import _authenticate_terminal_token, _decode_token


def _stream_claims() -> dict[str, object]:
    return {
        "sub": "stream-admin",
        "tenant_id": "stream-tenant",
        "role": "admin",
        "token_use": "control_center_stream",
        "cc_stream": True,
        "stream_user_id": "stream-admin",
        "stream_tenant_id": "stream-tenant",
        "exp": time.time() + 60,
    }


def test_stream_scope_is_pure_exact_and_identity_bound() -> None:
    claims = _stream_claims()

    assert control_center_stream_identity_is_bound(claims) is True
    assert (
        token_scope_allows_request(
            claims,
            method="GET",
            path="/api/events/stream",
        )
        is True
    )
    assert (
        token_scope_allows_request(
            claims,
            method="POST",
            path="/api/events/stream",
        )
        is False
    )
    assert (
        token_scope_allows_request(
            claims,
            method="GET",
            path="/ws/terminal",
        )
        is False
    )

    unbound = {**claims, "stream_tenant_id": "other-tenant"}
    assert control_center_stream_identity_is_bound(unbound) is False
    assert control_center_stream_identity_is_bound(
        {**claims, "sub": " stream-admin", "stream_user_id": " stream-admin"}
    ) is False
    assert control_center_stream_identity_is_bound(
        {**claims, "tenant_id": 7, "stream_tenant_id": 7}
    ) is False


def test_snake_stream_scope_is_exactly_user_tenant_snake_and_get_bound() -> None:
    claims = {
        "sub": "snake-user",
        "tenant_id": "snake-tenant",
        "role": "user",
        "token_use": "snake_events_stream",
        "stream_user_id": "snake-user",
        "stream_tenant_id": "snake-tenant",
        "stream_snake_id": "snake-a",
    }

    assert snake_events_stream_identity_is_bound(claims)
    assert token_scope_allows_request(
        claims,
        method="GET",
        path="/snakes/snake-a/events/stream",
    )
    assert not token_scope_allows_request(
        claims,
        method="GET",
        path="/snakes/snake-b/events/stream",
    )
    assert not token_scope_allows_request(
        claims,
        method="POST",
        path="/snakes/snake-a/events/stream-token",
    )
    assert not snake_events_stream_identity_is_bound(
        {**claims, "stream_tenant_id": "other-tenant"}
    )


def test_terminal_decoder_rejects_user_and_agent_signed_stream_derivatives() -> None:
    agent_secret = "terminal-agent-token-that-is-at-least-32-bytes"
    user_stream_token = jwt.encode(
        _stream_claims(),
        settings.secret_key,
        algorithm="HS256",
    )
    agent_stream_token = jwt.encode(
        _stream_claims(),
        agent_secret,
        algorithm="HS256",
    )
    full_user_token = jwt.encode(
        {
            "sub": "stream-admin",
            "tenant_id": "stream-tenant",
            "role": "admin",
            "exp": time.time() + 60,
        },
        settings.secret_key,
        algorithm="HS256",
    )

    assert _decode_token(user_stream_token, agent_secret) is None
    assert _decode_token(agent_stream_token, agent_secret) is None
    assert _decode_token(full_user_token, agent_secret)["sub"] == "stream-admin"


def test_terminal_central_auth_uses_production_agent_token_file(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AGENT_TOKEN_FILE", raising=False)
    service_token = "production-terminal-service-token-0123456789"
    token_file = tmp_path / "hub-agent-token"
    token_file.write_text(service_token, encoding="utf-8")
    token_file.chmod(0o600)
    app = Flask(__name__)
    app.config.update(AGENT_TOKEN="", AGENT_TOKEN_FILE=str(token_file))

    with app.test_request_context(
        "/ws/terminal",
        method="GET",
        headers={"Authorization": f"Bearer {service_token}"},
    ):
        payload, auth_mode, auth_required = _authenticate_terminal_token(
            service_token,
            app_config=app.config,
        )

    assert auth_required is True
    assert auth_mode == "agent_static_token"
    assert payload == {
        "sub": "agent_token",
        "role": "admin",
        "auth_mode": "agent_static_token",
    }


def test_terminal_central_auth_rejects_stream_derivative_with_file_secret(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AGENT_TOKEN_FILE", raising=False)
    service_token = "production-terminal-service-token-0123456789"
    token_file = tmp_path / "hub-agent-token"
    token_file.write_text(service_token, encoding="utf-8")
    token_file.chmod(0o600)
    stream_token = jwt.encode(
        _stream_claims(),
        settings.secret_key,
        algorithm="HS256",
    )
    app = Flask(__name__)
    app.config.update(AGENT_TOKEN="", AGENT_TOKEN_FILE=str(token_file))

    with app.test_request_context("/ws/terminal", method="GET"):
        payload, reason, auth_required = _authenticate_terminal_token(
            stream_token,
            app_config=app.config,
        )

    assert auth_required is True
    assert payload is None
    assert reason == "user_token_scope_forbidden"


def test_terminal_file_managed_auth_rejects_every_query_credential(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AGENT_TOKEN_FILE", raising=False)
    service_token = "production-terminal-service-token-0123456789"
    token_file = tmp_path / "hub-agent-token"
    token_file.write_text(service_token, encoding="utf-8")
    token_file.chmod(0o600)
    full_user_token = jwt.encode(
        {
            "sub": "terminal-admin",
            "tenant_id": "terminal-admin",
            "role": "admin",
            "exp": time.time() + 60,
        },
        settings.secret_key,
        algorithm="HS256",
    )
    app = Flask(__name__)
    app.config.update(AGENT_TOKEN="", AGENT_TOKEN_FILE=str(token_file))

    with app.test_request_context(f"/ws/terminal?token={full_user_token}", method="GET"):
        payload, reason, auth_required = _authenticate_terminal_token(
            full_user_token,
            app_config=app.config,
            token_from_query=True,
        )

    assert auth_required is True
    assert payload is None
    assert reason == "agent_token_query_forbidden"


def test_snake_optional_auth_rejects_stream_derivative_off_scope() -> None:
    app = Flask(__name__)
    stream_token = jwt.encode(
        _stream_claims(),
        settings.secret_key,
        algorithm="HS256",
    )
    full_user_token = jwt.encode(
        {
            "sub": "stream-admin",
            "tenant_id": "stream-tenant",
            "role": "admin",
            "exp": time.time() + 60,
        },
        settings.secret_key,
        algorithm="HS256",
    )

    with app.test_request_context(
        "/worker-context",
        method="POST",
        headers={"Authorization": f"Bearer {stream_token}"},
    ):
        assert _optional_user_auth() == {}

    with app.test_request_context(
        "/worker-context",
        method="POST",
        headers={"Authorization": f"Bearer {full_user_token}"},
    ):
        assert _optional_user_auth()["sub"] == "stream-admin"
