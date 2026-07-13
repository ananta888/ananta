from __future__ import annotations

import os
from pathlib import Path

import pytest
from flask import Flask

from agent.auth import (
    check_auth,
    check_strict_auth,
    resolve_configured_agent_token,
    rotate_token,
)
from agent.common.errors import PermanentError

SERVICE_TOKEN_A = "workflow-hub-service-token-a-0123456789abcdef"
SERVICE_TOKEN_B = "workflow-hub-service-token-b-0123456789abcdef"


def _write_secret(path: Path, value: str, *, mode: int = 0o600) -> None:
    path.write_text(f"{value}\n", encoding="utf-8")
    path.chmod(mode)


def _app(*, token_file: str, inline_token: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        AGENT_TOKEN=inline_token,
        AGENT_TOKEN_FILE=token_file,
    )

    @app.get("/strict")
    @check_strict_auth
    def strict_route():
        return {"status": "ok"}

    @app.get("/legacy")
    @check_auth
    def legacy_route():
        return {"status": "ok"}

    return app


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_strict_auth_accepts_a_file_managed_service_token(tmp_path: Path) -> None:
    token_file = tmp_path / "hub-service-token"
    _write_secret(token_file, SERVICE_TOKEN_A)
    client = _app(token_file=str(token_file)).test_client()

    accepted = client.get("/strict", headers=_bearer(SERVICE_TOKEN_A))
    rejected = client.get("/strict", headers=_bearer(SERVICE_TOKEN_B))

    assert accepted.status_code == 200
    assert rejected.status_code == 401
    assert rejected.get_json()["data"]["reason_code"] == "workflow_auth_invalid"
    with client.application.app_context():
        assert resolve_configured_agent_token() == SERVICE_TOKEN_A


def test_file_managed_service_token_is_never_accepted_from_query_string(tmp_path: Path) -> None:
    token_file = tmp_path / "hub-service-token"
    _write_secret(token_file, SERVICE_TOKEN_A)
    client = _app(token_file=str(token_file)).test_client()

    response = client.get(f"/legacy?token={SERVICE_TOKEN_A}")

    assert response.status_code == 401


def test_file_managed_token_is_reloaded_after_atomic_rotation(tmp_path: Path) -> None:
    token_file = tmp_path / "hub-service-token"
    replacement = tmp_path / "hub-service-token.next"
    _write_secret(token_file, SERVICE_TOKEN_A)
    client = _app(token_file=str(token_file)).test_client()
    assert client.get("/strict", headers=_bearer(SERVICE_TOKEN_A)).status_code == 200

    _write_secret(replacement, SERVICE_TOKEN_B)
    os.replace(replacement, token_file)

    assert client.get("/strict", headers=_bearer(SERVICE_TOKEN_B)).status_code == 200
    assert client.get("/strict", headers=_bearer(SERVICE_TOKEN_A)).status_code == 401


@pytest.mark.parametrize(
    ("token_file", "inline_token", "prepare"),
    [
        ("relative/token", None, None),
        ("{path}", None, lambda path: _write_secret(path, "too-short")),
        ("{path}", None, lambda path: _write_secret(path, SERVICE_TOKEN_A, mode=0o660)),
        ("{path}", SERVICE_TOKEN_B, lambda path: _write_secret(path, SERVICE_TOKEN_A)),
    ],
    ids=("relative-reference", "short-token", "group-writable", "inline-conflict"),
)
def test_unsafe_file_token_configuration_fails_closed(
    tmp_path: Path,
    token_file: str,
    inline_token: str | None,
    prepare,
) -> None:
    path = tmp_path / "hub-service-token"
    if prepare is not None:
        prepare(path)
    configured_path = token_file.format(path=path)
    client = _app(token_file=configured_path, inline_token=inline_token).test_client()

    response = client.get("/strict", headers=_bearer(SERVICE_TOKEN_A))

    assert response.status_code == 503
    assert response.get_json()["data"]["reason_code"] == "workflow_auth_configuration_invalid"


def test_inline_and_file_token_may_match_during_migration(tmp_path: Path) -> None:
    token_file = tmp_path / "hub-service-token"
    _write_secret(token_file, SERVICE_TOKEN_A)
    client = _app(token_file=str(token_file), inline_token=SERVICE_TOKEN_A).test_client()

    assert client.get("/strict", headers=_bearer(SERVICE_TOKEN_A)).status_code == 200


def test_application_rotation_is_blocked_for_externally_managed_token(tmp_path: Path) -> None:
    token_file = tmp_path / "hub-service-token"
    _write_secret(token_file, SERVICE_TOKEN_A)
    app = _app(token_file=str(token_file))

    with app.app_context(), pytest.raises(PermanentError, match="AGENT_TOKEN_FILE"):
        rotate_token()
