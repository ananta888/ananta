from __future__ import annotations

import time

import jwt
import pytest
from flask import Flask

import agent.auth as auth
from agent.config import settings
from agent.routes.source_control_public_remotes import (
    PUBLIC_REMOTE_ROUTE_MATRIX,
    create_source_control_public_remotes_blueprint,
)
from agent.routes.source_control_v1 import (
    SOURCE_CONTROL_V1_AUTHORIZATION_MATRIX,
    create_source_control_v1_blueprint,
)
from agent.routes.source_control_workspace_registrations import (
    WORKSPACE_REGISTRATION_ROUTE_MATRIX,
    create_source_control_workspace_registrations_blueprint,
)
from agent.services.source_control_access_policy import (
    HubSourcePrincipal,
    SourceControlAccessPolicy,
    SourceControlAction,
    SourceObjectBinding,
)


class _Api:
    def binding(self, *, resource_kind, resource_id):
        return {
            "tenant_id": "tenant-example",
            "project_id": "project-example",
            "owner_id": "owner-example",
        }

    def __getattr__(self, name):
        def call(**kwargs):
            if name in {
                "get_connection",
                "context_policy_detail",
                "context_policy_active",
            }:
                return ({}, "etag")
            return {}

        return call


def _token(*, roles=("user",)):
    return jwt.encode(
        {
            "sub": "owner-example",
            "tenant_id": "tenant-example",
            "project_id": "project-example",
            "roles": list(roles),
            "exp": time.time() + 300,
        },
        settings.secret_key,
        algorithm="HS256",
    )


def _path(rule: str) -> str:
    return (
        "/api/source-control/v1"
        + rule.replace("<connection_id>", "connection-example")
        .replace("<index_id>", "index-example")
        .replace("<artifact_id>", "artifact-example")
        .replace("<grant_id>", "grant-example")
        .replace("<policy_id>", "policy-example")
        .replace("<workspace_id>", "workspace-example")
        .replace("<int:version>", "1")
    )


_PROJECT_SELECTOR_SURFACES = (
    (
        "source_control_public_remotes",
        create_source_control_public_remotes_blueprint,
        PUBLIC_REMOTE_ROUTE_MATRIX,
    ),
    (
        "source_control_workspace_registrations",
        create_source_control_workspace_registrations_blueprint,
        WORKSPACE_REGISTRATION_ROUTE_MATRIX,
    ),
)

_PROJECT_SELECTOR_CASES = tuple(
    (blueprint_name, factory, entry)
    for blueprint_name, factory, matrix in _PROJECT_SELECTOR_SURFACES
    for entry in matrix
)


def test_matrix_covers_every_v1_flask_rule(monkeypatch) -> None:
    monkeypatch.setattr(
        auth,
        "resolve_configured_agent_token",
        lambda config=None: "a" * 32,
    )
    app = Flask(__name__)
    app.register_blueprint(create_source_control_v1_blueprint(_Api()))
    actual = {
        (
            rule.endpoint.rsplit(".", 1)[-1],
            rule.rule.removeprefix("/api/source-control/v1"),
            tuple(
                sorted(
                    method
                    for method in rule.methods
                    if method not in {"HEAD", "OPTIONS"}
                )
            ),
        )
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("source_control_v1.")
    }
    expected = {
        (item.endpoint, item.rule, tuple(sorted(item.methods)))
        for item in SOURCE_CONTROL_V1_AUTHORIZATION_MATRIX
    }
    assert actual == expected


def test_project_selector_matrices_cover_every_flask_rule() -> None:
    app = Flask(__name__)
    for _, factory, _ in _PROJECT_SELECTOR_SURFACES:
        app.register_blueprint(factory(_Api()))
    blueprint_names = {
        blueprint_name for blueprint_name, _, _ in _PROJECT_SELECTOR_SURFACES
    }
    actual = {
        (
            rule.endpoint.split(".", 1)[0],
            rule.endpoint.rsplit(".", 1)[-1],
            rule.rule.removeprefix("/api/source-control/v1"),
            tuple(
                sorted(
                    method
                    for method in rule.methods
                    if method not in {"HEAD", "OPTIONS"}
                )
            ),
        )
        for rule in app.url_map.iter_rules()
        if rule.endpoint.split(".", 1)[0] in blueprint_names
    }
    expected = {
        (
            blueprint_name,
            entry.endpoint,
            entry.rule,
            tuple(sorted(entry.methods)),
        )
        for blueprint_name, _, entry in _PROJECT_SELECTOR_CASES
    }
    assert actual == expected


@pytest.mark.parametrize("entry", SOURCE_CONTROL_V1_AUTHORIZATION_MATRIX)
def test_every_v1_rule_is_unauthenticated_fail_closed(
    monkeypatch, entry
) -> None:
    monkeypatch.setattr(
        auth,
        "resolve_configured_agent_token",
        lambda config=None: "a" * 32,
    )
    app = Flask(__name__)
    app.register_blueprint(create_source_control_v1_blueprint(_Api()))
    response = app.test_client().open(
        _path(entry.rule),
        method=entry.methods[0],
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("blueprint_name", "factory", "entry"),
    _PROJECT_SELECTOR_CASES,
)
def test_project_selector_rules_are_unauthenticated_fail_closed(
    monkeypatch,
    blueprint_name,
    factory,
    entry,
) -> None:
    del blueprint_name
    monkeypatch.setattr(
        auth,
        "resolve_configured_agent_token",
        lambda config=None: "a" * 32,
    )
    app = Flask(__name__)
    app.register_blueprint(factory(_Api()))
    response = app.test_client().open(
        _path(entry.rule),
        method=entry.methods[0],
        json={},
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("blueprint_name", "factory", "entry"),
    _PROJECT_SELECTOR_CASES,
)
def test_project_selector_rules_require_query_scope_and_hide_mismatch(
    monkeypatch,
    blueprint_name,
    factory,
    entry,
) -> None:
    del blueprint_name
    monkeypatch.setattr(
        auth,
        "resolve_configured_agent_token",
        lambda config=None: "a" * 32,
    )
    app = Flask(__name__)
    app.register_blueprint(factory(_Api()))
    client = app.test_client()
    headers = {"Authorization": f"Bearer {_token(roles=('admin',))}"}

    missing = client.open(
        _path(entry.rule),
        method=entry.methods[0],
        headers=headers,
        json={},
    )
    mismatched = client.open(
        f"{_path(entry.rule)}?project_id=project-other",
        method=entry.methods[0],
        headers=headers,
        json={},
    )

    assert missing.status_code == 400
    assert missing.get_json()["error"]["code"] == "project_id_required"
    assert mismatched.status_code == 404
    assert mismatched.get_json()["error"]["code"] == (
        "source_control_not_found"
    )


@pytest.mark.parametrize("entry", SOURCE_CONTROL_V1_AUTHORIZATION_MATRIX)
def test_matrix_actions_hide_foreign_objects_and_enforce_roles(entry) -> None:
    policy = SourceControlAccessPolicy()
    user = HubSourcePrincipal(
        subject_id="owner-example",
        tenant_id="tenant-example",
        project_id="project-example",
        roles=frozenset({"user"}),
    )
    owner = HubSourcePrincipal(
        subject_id="owner-example",
        tenant_id="tenant-example",
        project_id="project-example",
        roles=frozenset({"project_owner"}),
    )
    admin = HubSourcePrincipal(
        subject_id="admin-example",
        tenant_id="tenant-example",
        project_id="project-example",
        roles=frozenset({"admin"}),
    )
    foreign = SourceObjectBinding(
        object_id="object-example",
        tenant_id="tenant-other",
        project_id="project-example",
    )
    local = SourceObjectBinding(
        object_id="object-example",
        tenant_id="tenant-example",
        project_id="project-example",
        owner_id="owner-example",
    )
    assert policy.authorize(
        principal=owner,
        action=entry.action,
        binding=foreign,
    ).status_code == 404
    if entry.action is SourceControlAction.policy:
        assert policy.authorize(
            principal=owner, action=entry.action, binding=local
        ).status_code == 403
        assert policy.authorize(
            principal=admin, action=entry.action, binding=local
        ).allowed
    elif entry.action in {
        SourceControlAction.refresh,
        SourceControlAction.scan,
        SourceControlAction.index,
        SourceControlAction.delete,
    }:
        assert policy.authorize(
            principal=user, action=entry.action, binding=local
        ).status_code == 403
        assert policy.authorize(
            principal=owner, action=entry.action, binding=local
        ).allowed
        assert policy.authorize(
            principal=admin, action=entry.action, binding=local
        ).allowed
