from __future__ import annotations

import base64
import json

import pytest

import agent.ai_agent as ai_agent
from agent.bootstrap.source_control_api import register_source_control_api
from agent.services.source_access_manifest_keyring import (
    SourceAccessManifestKeyringError,
)


def test_worker_bootstrap_builds_authenticated_governed_handler(monkeypatch) -> None:
    monkeypatch.delenv("ANANTA_SOURCE_ACCESS_KEYRING_FILE", raising=False)
    monkeypatch.setenv(
        "ANANTA_SOURCE_ACCESS_ALLOW_COMPOSE_SECRET_DERIVATION", "1"
    )
    monkeypatch.setenv("ANANTA_RUNTIME_PROFILE", "compose-safe")
    monkeypatch.setenv("ANANTA_QUICKSTART_MODE", "role")
    monkeypatch.setenv(
        "SECRET_KEY", "worker-compose-secret-with-at-least-32-bytes"
    )
    monkeypatch.setenv("ANANTA_KNOWLEDGE_INDEX_WORKER_ID", "worker-alpha")

    handler = ai_agent._build_governed_knowledge_index_handler_from_environment()

    assert handler._worker_id == "worker-alpha"
    assert handler._source_access_manifest_verifier is not None
    assert handler._allow_legacy_unsigned_source_dispatch is False


def test_worker_bootstrap_uses_file_managed_source_access_keyring(
    monkeypatch,
    tmp_path,
) -> None:
    keyring_path = tmp_path / "source-access-hmac-keyring.json"
    keyring_path.write_text(
        json.dumps(
            {
                "schema": "ananta.source-access-hmac-keyring.v1",
                "active_key_id": "test-source-access-v1",
                "keys": {
                    "test-source-access-v1": base64.b64encode(
                        b"test-source-access-secret-material"
                    ).decode("ascii")
                },
            }
        ),
        encoding="utf-8",
    )
    keyring_path.chmod(0o600)
    monkeypatch.setenv("ANANTA_SOURCE_ACCESS_KEYRING_FILE", str(keyring_path))
    monkeypatch.setenv(
        "ANANTA_SOURCE_ACCESS_ALLOW_COMPOSE_SECRET_DERIVATION", "0"
    )
    monkeypatch.setenv("SECRET_KEY", "")
    monkeypatch.setenv("ANANTA_KNOWLEDGE_INDEX_WORKER_ID", "worker-alpha")

    handler = ai_agent._build_governed_knowledge_index_handler_from_environment()

    assert handler._worker_id == "worker-alpha"
    assert handler._source_access_manifest_verifier is not None
    assert handler._allow_legacy_unsigned_source_dispatch is False


def test_worker_bootstrap_fails_closed_without_production_keyring(
    monkeypatch,
) -> None:
    monkeypatch.delenv("ANANTA_SOURCE_ACCESS_KEYRING_FILE", raising=False)
    monkeypatch.delenv(
        "ANANTA_SOURCE_ACCESS_ALLOW_COMPOSE_SECRET_DERIVATION", raising=False
    )
    monkeypatch.setenv("ANANTA_RUNTIME_PROFILE", "production")
    monkeypatch.setenv("ANANTA_KNOWLEDGE_INDEX_WORKER_ID", "worker-alpha")

    with pytest.raises(SourceAccessManifestKeyringError) as error:
        ai_agent._build_governed_knowledge_index_handler_from_environment()

    assert error.value.reason_code == "source_access_keyring_required"


def test_source_control_routes_are_not_composed_in_a_worker(monkeypatch) -> None:
    monkeypatch.setattr(ai_agent.settings, "role", "worker")
    app = ai_agent.Flask(__name__)

    register_source_control_api(app)

    assert app.extensions["source_control_api_registration"] == {
        "ready": False,
        "reason_code": "source_control_hub_role_required",
    }
    assert "source_control_v1_registered" not in app.extensions
