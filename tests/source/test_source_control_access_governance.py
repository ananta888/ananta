from __future__ import annotations

import time
from dataclasses import dataclass

import jwt
import pytest
from flask import Flask

import agent.auth as auth
from agent.config import settings
from agent.routes import (
    codecompass_domain_scope,
    codecompass_graph,
    codecompass_reload,
    context_policy,
    control_center_api,
    knowledge,
    sources,
)
from agent.services.source_control_access_policy import (
    HubSourcePrincipal,
    SourceControlAccessPolicy,
    SourceControlAction,
    SourceObjectBinding,
)


@dataclass
class ScopedObject:
    id: str
    tenant_id: str
    project_id: str
    owner_id: str

    def model_dump(self):
        return vars(self)


class ObjectRepository:
    def __init__(self, value):
        self.value = value

    def get_by_id(self, object_id):
        return self.value if object_id == self.value.id else None


class SourceRegistry:
    def __init__(self, value):
        self.value = value

    def get_source(self, source_id):
        return self.value if source_id == self.value["source_id"] else None


def _token(*, subject, tenant, project, roles=("user",)):
    return jwt.encode(
        {
            "sub": subject,
            "tenant_id": tenant,
            "project_id": project,
            "roles": list(roles),
            "exp": time.time() + 300,
        },
        settings.secret_key,
        algorithm="HS256",
    )


def _headers(**claims):
    return {"Authorization": f"Bearer {_token(**claims)}"}


def _app(monkeypatch, blueprint):
    monkeypatch.setattr(
        auth,
        "resolve_configured_agent_token",
        lambda config=None: "a" * 32,
    )
    app = Flask(__name__)
    app.register_blueprint(blueprint)
    app.testing = True
    return app


@pytest.mark.parametrize("action", tuple(SourceControlAction))
def test_foreign_objects_are_hidden_for_every_action(action) -> None:
    policy = SourceControlAccessPolicy()
    principal = HubSourcePrincipal(
        subject_id="user-1",
        tenant_id="tenant-1",
        project_id="project-1",
        roles=frozenset({"project_owner"}),
    )
    binding = SourceObjectBinding(
        object_id="object-1",
        tenant_id="tenant-2",
        project_id="project-1",
        owner_id="user-1",
    )

    decision = policy.authorize(
        principal=principal,
        action=action,
        binding=binding,
    )

    assert decision.status_code == 404
    assert decision.reason_code == "resource_not_found"


@pytest.mark.parametrize(
    "action",
    (
        SourceControlAction.refresh,
        SourceControlAction.scan,
        SourceControlAction.index,
        SourceControlAction.delete,
    ),
)
def test_same_object_role_only_mutation_denial_is_403(action) -> None:
    decision = SourceControlAccessPolicy().authorize(
        principal=HubSourcePrincipal(
            subject_id="user-1",
            tenant_id="tenant-1",
            project_id="project-1",
            roles=frozenset({"user"}),
        ),
        action=action,
        binding=SourceObjectBinding(
            object_id="object-1",
            tenant_id="tenant-1",
            project_id="project-1",
            owner_id="user-1",
        ),
    )

    assert decision.status_code == 403
    assert decision.reason_code == "source_control_mutation_role_required"


def test_unbound_legacy_objects_are_admin_only() -> None:
    binding = SourceObjectBinding(
        object_id="legacy-1",
        tenant_id=None,
        project_id=None,
    )
    policy = SourceControlAccessPolicy()
    user = HubSourcePrincipal(
        subject_id="user-1",
        tenant_id="tenant-1",
        project_id="project-1",
        roles=frozenset({"project_owner"}),
    )
    admin = HubSourcePrincipal(
        subject_id="admin-1",
        tenant_id=None,
        project_id=None,
        roles=frozenset({"admin"}),
    )

    assert policy.authorize(
        principal=user,
        action=SourceControlAction.detail,
        binding=binding,
    ).status_code == 404
    admin_decision = policy.authorize(
        principal=admin,
        action=SourceControlAction.detail,
        binding=binding,
    )
    assert admin_decision.allowed is True
    assert admin_decision.legacy_admin_access is True


_FOREIGN_CLAIMS = (
    {"subject": "user-1", "tenant": "tenant-2", "project": "project-1"},
    {"subject": "user-1", "tenant": "tenant-1", "project": "project-2"},
    {"subject": "user-2", "tenant": "tenant-1", "project": "project-1"},
)


@pytest.mark.parametrize("claims", _FOREIGN_CLAIMS)
def test_sources_blueprint_hides_cross_scope_and_cross_object(
    monkeypatch,
    claims,
) -> None:
    monkeypatch.setattr(
        sources,
        "_registry",
        lambda: SourceRegistry(
            {
                "source_id": "source-1",
                "source_type": "text",
                "tenant_id": "tenant-1",
                "project_id": "project-1",
                "owner_id": "user-1",
            }
        ),
    )
    app = _app(monkeypatch, sources.sources_bp)

    response = app.test_client().get(
        "/sources/source-1",
        headers=_headers(**claims),
    )

    assert response.status_code == 404


@pytest.mark.parametrize("claims", _FOREIGN_CLAIMS)
def test_knowledge_blueprint_hides_cross_scope_and_cross_object(
    monkeypatch,
    claims,
) -> None:
    row = ScopedObject("collection-1", "tenant-1", "project-1", "user-1")
    monkeypatch.setattr(
        knowledge,
        "_collection_repo",
        lambda: ObjectRepository(row),
    )
    app = _app(monkeypatch, knowledge.knowledge_bp)

    response = app.test_client().get(
        "/knowledge/collections/collection-1",
        headers=_headers(**claims),
    )

    assert response.status_code == 404


@pytest.mark.parametrize("claims", _FOREIGN_CLAIMS)
def test_codecompass_graph_blueprint_hides_index_existence(
    monkeypatch,
    claims,
) -> None:
    row = ScopedObject("index-1", "tenant-1", "project-1", "user-1")
    monkeypatch.setattr(
        codecompass_graph,
        "_knowledge_index_repo",
        lambda: ObjectRepository(row),
    )
    app = _app(monkeypatch, codecompass_graph.codecompass_graph_bp)

    response = app.test_client().get(
        "/api/codecompass/graph?knowledge_index_id=index-1",
        headers=_headers(**claims),
    )

    assert response.status_code == 404


@pytest.mark.parametrize("claims", _FOREIGN_CLAIMS)
def test_codecompass_reload_blueprint_hides_task_existence(
    monkeypatch,
    claims,
) -> None:
    row = ScopedObject("task-1", "tenant-1", "project-1", "user-1")
    monkeypatch.setattr(
        codecompass_reload,
        "_task_for_policy",
        lambda task_id: row if task_id == row.id else None,
    )
    app = _app(monkeypatch, codecompass_reload.codecompass_reload_bp)

    response = app.test_client().post(
        "/api/codecompass/reload-context",
        json={"task_id": "task-1", "request": {}},
        headers=_headers(**claims),
    )

    assert response.status_code == 404


def test_unbound_codecompass_domain_catalog_is_hidden_from_users(
    monkeypatch,
) -> None:
    app = _app(
        monkeypatch,
        codecompass_domain_scope.codecompass_domain_scope_bp,
    )

    response = app.test_client().get(
        "/api/codecompass/domains",
        headers=_headers(
            subject="user-1",
            tenant="tenant-1",
            project="project-1",
        ),
    )

    assert response.status_code == 404


def test_context_policy_blueprint_keeps_policy_admin_only(monkeypatch) -> None:
    app = _app(monkeypatch, context_policy.context_policy_bp)

    response = app.test_client().get(
        "/api/context-policy/policies",
        headers=_headers(
            subject="owner-1",
            tenant="tenant-1",
            project="project-1",
            roles=("project_owner",),
        ),
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("get", "/api/codecompass/context-scopes"),
        ("post", "/api/codecompass/context-scopes/preview"),
    ),
)
def test_control_center_codecompass_scopes_are_admin_legacy_only(
    monkeypatch,
    method,
    path,
) -> None:
    app = _app(monkeypatch, control_center_api.control_center_api_bp)
    client = app.test_client()

    response = getattr(client, method)(
        path,
        json={} if method == "post" else None,
        headers=_headers(
            subject="user-1",
            tenant="tenant-1",
            project="project-1",
        ),
    )

    assert response.status_code == 404
