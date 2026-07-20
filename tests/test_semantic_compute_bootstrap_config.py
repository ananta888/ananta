from __future__ import annotations

from flask import Flask

from agent.bootstrap.semantic_media_services import initialize_semantic_media_services
from agent.repositories.semantic_media_capability_grant_repository import (
    SqlSemanticMediaCapabilityGrantRepository,
)
from agent.services.semantic_media_permission_service import SemanticMediaPermissionService


def test_semantic_compute_security_confirmation_is_fail_closed(monkeypatch) -> None:
    monkeypatch.delenv("ANANTA_SEMANTIC_COMPUTE_SECURITY_CONFIRMED", raising=False)
    monkeypatch.setenv("ANANTA_PEER_EVIDENCE_SYNC_ENABLED", "false")
    monkeypatch.setenv("ANANTA_SPEECH_ADAPTATION_TRAINING_ENABLED", "false")
    app = Flask(__name__)
    app.secret_key = "test-secret-not-used-for-contract-signing"

    initialize_semantic_media_services(app)

    assert app.config["SEMANTIC_COMPUTE_SECURITY_CONFIRMED"] is False
    assert app.config["SEMANTIC_COMPUTE_FALLBACK_HEALTHY"] is True
    assert isinstance(
        app.extensions["semantic_media_capability_grant_repository"],
        SqlSemanticMediaCapabilityGrantRepository,
    )
    assert isinstance(
        app.extensions["semantic_media_permission_service"],
        SemanticMediaPermissionService,
    )


def test_semantic_compute_operator_confirmation_is_loaded_once_at_startup(monkeypatch) -> None:
    monkeypatch.setenv("ANANTA_SEMANTIC_COMPUTE_SECURITY_CONFIRMED", "true")
    monkeypatch.setenv("ANANTA_SEMANTIC_COMPUTE_FALLBACK_HEALTHY", "false")
    monkeypatch.setenv("ANANTA_PEER_EVIDENCE_SYNC_ENABLED", "false")
    monkeypatch.setenv("ANANTA_SPEECH_ADAPTATION_TRAINING_ENABLED", "false")
    app = Flask(__name__)
    app.secret_key = "test-secret-not-used-for-contract-signing"

    initialize_semantic_media_services(app)

    assert app.config["SEMANTIC_COMPUTE_SECURITY_CONFIRMED"] is True
    assert app.config["SEMANTIC_COMPUTE_FALLBACK_HEALTHY"] is False
