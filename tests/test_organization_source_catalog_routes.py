from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask, g

from agent.models.organization_source_catalog_models import (
    OrganizationSourceCatalogPublishResult,
)
from agent.routes import organization_source_catalogs as routes
from agent.services.project_access_authority import ProjectCapability


class _Publisher:
    def __init__(self, *, replayed: bool = False) -> None:
        self.replayed = replayed
        self.calls: list[dict[str, Any]] = []

    def publish(self, **kwargs):
        self.calls.append(kwargs)
        return OrganizationSourceCatalogPublishResult(
            organization_id=kwargs["organization_id"],
            catalog_task_id="source-catalog-task-1",
            catalog_id="catalog-0123456789abcdef",
            catalog_hash="1" * 64,
            repository_revision="2" * 64,
            manifest_hash="3" * 64,
            source_allowlist_version="1" * 64,
            source_scope="organization:org-1",
            source_count=2,
            replayed=self.replayed,
        )


@pytest.fixture()
def app() -> Flask:
    application = Flask(__name__)
    application.register_blueprint(routes.organization_source_catalogs_bp)
    return application


def _install_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    def require_scope(
        organization_id: str,
        capability: ProjectCapability,
        *,
        include_archived: bool,
    ):
        assert organization_id == "org-1"
        assert capability is ProjectCapability.MANAGE
        assert include_archived is False
        return SimpleNamespace(
            principal=SimpleNamespace(
                subject_id="operator-1",
                roles=frozenset({"admin"}),
            ),
            project=SimpleNamespace(role="owner"),
            tenant_id="tenant-1",
            project_id="project-1",
            organization_id="org-1",
        )

    monkeypatch.setattr(routes, "require_organization_scope", require_scope)


def test_route_accepts_only_retrieval_intent_and_returns_readiness_selectors(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_scope(monkeypatch)
    publisher = _Publisher()
    app.extensions["organization_source_catalog_publisher_service"] = publisher
    with app.test_request_context(
        "/api/organizations/org-1/source-catalogs",
        method="POST",
        headers={"Idempotency-Key": "catalog-publish-key-1"},
        json={
            "connection_id": "connection-1",
            "queries": ["HRM", "planning"],
            "limit": 20,
        },
    ):
        g.user = {"sub": "operator-1"}
        response, status = routes.publish_organization_source_catalog.__wrapped__(
            "org-1"
        )

    assert status == 201
    data = response.get_json()["data"]
    assert data == {
        "schema": "organization_source_catalog_publication.v1",
        "organization_id": "org-1",
        "catalog_task_id": "source-catalog-task-1",
        "catalog_id": "catalog-0123456789abcdef",
        "catalog_hash": "1" * 64,
        "repository_revision": "2" * 64,
        "manifest_hash": "3" * 64,
        "source_allowlist_version": "1" * 64,
        "source_scope": "organization:org-1",
        "source_count": 2,
        "replayed": False,
    }
    call = publisher.calls[0]
    assert call["command"].model_dump() == {
        "connection_id": "connection-1",
        "queries": ["HRM", "planning"],
        "limit": 20,
    }
    assert call["principal"].project_role == "owner"
    assert call["principal"].credential_type == "user"


def test_route_rejects_caller_supplied_evidence_identity_before_service(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_scope(monkeypatch)
    publisher = _Publisher()
    app.extensions["organization_source_catalog_publisher_service"] = publisher
    with app.test_request_context(
        "/api/organizations/org-1/source-catalogs",
        method="POST",
        headers={"Idempotency-Key": "catalog-publish-key-1"},
        json={
            "connection_id": "connection-1",
            "queries": ["HRM"],
            "source_ids": ["SRC_9999"],
            "revision": "caller-revision",
        },
    ):
        g.user = {"sub": "operator-1"}
        response, status = routes.publish_organization_source_catalog.__wrapped__(
            "org-1"
        )

    assert status == 400
    assert response.get_json()["message"] == "organization_payload_fields_invalid"
    assert publisher.calls == []


def test_worker_credential_classification_wins_over_truthy_user(app: Flask) -> None:
    with app.test_request_context("/"):
        g.user = {"sub": "must-not-shadow-worker"}
        g.auth_payload = {
            "auth_mode": "registered_worker_service_token",
            "token_use": "workflow_worker_service",
        }
        g.service_identity = {"worker_id": "worker-1"}
        assert routes._credential_type() == "worker"
