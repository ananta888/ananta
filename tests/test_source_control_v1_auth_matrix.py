from __future__ import annotations

import time

import jwt
import pytest
from flask import Flask

import agent.auth as auth
from agent.config import settings
from agent.routes.source_control_v1 import (
    SOURCE_CONTROL_V1_AUTHORIZATION_MATRIX,
    create_source_control_v1_blueprint,
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
        .replace("<int:version>", "1")
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
