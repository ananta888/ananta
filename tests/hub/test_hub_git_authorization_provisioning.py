from __future__ import annotations

import json
import time

import jwt
import pytest
from flask import Flask
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import agent.auth as auth
from agent.config import settings
from agent.repositories.hub_git_authorization_repository import (
    SQLHubGitAuthorizationRepository,
)
from agent.routes.source_control_git_authorizations import (
    GIT_AUTHORIZATION_ROUTE_MATRIX,
    create_source_control_git_authorizations_blueprint,
)
from tests.project_access_fakes import AllowProjectAccess
from agent.services.hub_git_authorization_provisioning import (
    GitAuthorizationProviderHealth,
    HubGitAuthorizationProvisioningError,
    HubGitAuthorizationProvisioningService,
    ProvisionedGitAuthorization,
    UnavailableHubGitAuthorizationProvisioner,
)
from agent.services.source_control_api_runtime import (
    SQLSourceControlOperationStore,
)


class _Provider:
    def __init__(self) -> None:
        self.calls = 0

    def resolve_authorization(self, request):
        self.calls += 1
        selection = request.selection
        return ProvisionedGitAuthorization(
            connection_ref=selection.authorization_handle,
            authorization_kind=selection.authorization_kind,
            remote_url=(
                "https://github.com/owner/repository.git"
                if selection.authorization_kind.startswith("github_")
                else "ssh://git@git.example.test/team/repository.git"
            ),
            credential_ref="vault://git/opaque-reference",
            credential_username="x-access-token",
            authorization_state="active",
            granted_scopes=frozenset(
                {"contents:read"}
                if selection.authorization_kind.startswith("github_")
                else {"repository:read"}
            ),
            repository=selection.repository,
        )

    def health(self, *, scope):
        del scope
        return GitAuthorizationProviderHealth(status="healthy")


class _Policy:
    def __init__(self) -> None:
        self.requests = []

    def authorize(self, request):
        self.requests.append(request)
        return object()


def _service(provider=None):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    selected_provider = provider or _Provider()
    service = HubGitAuthorizationProvisioningService(
        repository=SQLHubGitAuthorizationRepository(
            session_factory=lambda: Session(engine)
        ),
        provider=selected_provider,
        remote_policy=_Policy(),
        idempotency=SQLSourceControlOperationStore(engine),
        connector_types=lambda: ("generic_git", "github_repository"),
        secret_resolver_ready=lambda: True,
    )
    return service, selected_provider


def _principal(*, roles=("project_owner",), project="project-1"):
    from agent.services.source_control_access_policy import HubSourcePrincipal

    return HubSourcePrincipal(
        subject_id="owner-1",
        tenant_id="tenant-1",
        project_id=project,
        roles=frozenset(roles),
    )


def _selection(**extra):
    return {
        "authorization_handle": "github-installation:42",
        "authorization_kind": "github_app",
        "repository": "owner/repository",
        **extra,
    }


def _token(*, roles=("project_owner",), project="project-1"):
    return jwt.encode(
        {
            "sub": "owner-1",
            "tenant_id": "tenant-1",
            "project_id": project,
            "roles": list(roles),
            "exp": time.time() + 300,
        },
        settings.secret_key,
        algorithm="HS256",
    )


def _headers(*, roles=("project_owner",), project="project-1"):
    return {
        "Authorization": f"Bearer {_token(roles=roles, project=project)}",
        "Idempotency-Key": "git-auth:test-operation-0001",
    }


def test_provision_is_durably_idempotent_and_returns_no_provider_material():
    service, provider = _service()

    first = service.provision(
        principal=_principal(),
        payload=_selection(),
        idempotency_key="git-auth:service-operation-0001",
    )
    replay = service.provision(
        principal=_principal(),
        payload=_selection(),
        idempotency_key="git-auth:service-operation-0001",
    )

    assert replay == first
    assert provider.calls == 1
    encoded = json.dumps(first)
    assert "remote_url" not in encoded
    assert "credential_ref" not in encoded
    assert "github.com" not in encoded
    assert first["credential_configured"] is True
    assert first["current_revision"] == 1


@pytest.mark.parametrize(
    "forbidden",
    ("token", "credential_ref", "clone_url", "remote_url"),
)
def test_browser_provider_material_is_rejected_before_provider_call(forbidden):
    service, provider = _service()

    with pytest.raises(
        HubGitAuthorizationProvisioningError,
        match="git_authorization_selection_fields_invalid",
    ):
        service.provision(
            principal=_principal(),
            payload=_selection(**{forbidden: "forbidden"}),
            idempotency_key="git-auth:service-operation-0002",
        )

    assert provider.calls == 0


def test_list_detail_revoke_and_scope_are_owner_scoped_and_cas_guarded():
    service, _ = _service()
    created = service.provision(
        principal=_principal(),
        payload=_selection(),
        idempotency_key="git-auth:service-operation-0003",
    )

    assert service.list_authorizations(
        principal=_principal(),
        cursor=None,
        limit=50,
        authorization_kind=None,
        authorization_state=None,
    )["items"] == [created]
    assert service.list_authorizations(
        principal=_principal(project="project-foreign"),
        cursor=None,
        limit=50,
        authorization_kind=None,
        authorization_state=None,
    )["items"] == []
    revoked = service.revoke(
        principal=_principal(),
        authorization_ref="github-installation:42",
        repository="owner/repository",
        expected_revision=1,
        idempotency_key="git-auth:service-operation-0004",
    )
    assert revoked["authorization_state"] == "revoked"
    assert revoked["granted_scopes"] == []
    assert revoked["current_revision"] == 2


def test_unavailable_external_provider_fails_closed():
    service, _ = _service(UnavailableHubGitAuthorizationProvisioner())

    with pytest.raises(
        HubGitAuthorizationProvisioningError,
        match="git_authorization_provider_unavailable",
    ):
        service.validate(
            principal=_principal(),
            payload=_selection(),
        )
    assert service.health(principal=_principal())["status"] == "unavailable"


def test_http_matrix_is_authenticated_and_role_guarded(monkeypatch):
    monkeypatch.setattr(
        auth,
        "resolve_configured_agent_token",
        lambda config=None: "a" * 32,
    )
    service, _ = _service()
    app = Flask(__name__)
    app.extensions["project_access_authority"] = AllowProjectAccess(
        role="viewer"
    )
    app.register_blueprint(
        create_source_control_git_authorizations_blueprint(service)
    )
    client = app.test_client()

    for entry in GIT_AUTHORIZATION_ROUTE_MATRIX:
        path = (
            "/api/source-control/v1"
            + entry.rule.replace(
                "<authorization_ref>", "github-installation:42"
            )
        )
        response = client.open(path, method=entry.methods[0])
        assert response.status_code == 401

    denied = client.post(
        "/api/source-control/v1/git-authorizations/validate",
        json=_selection(),
        headers=_headers(roles=("user",)),
    )
    assert denied.status_code == 403
    assert denied.get_json()["data"]["reason_code"] == (
        "source_control_mutation_role_required"
    )


def test_http_provision_detail_and_revoke_never_expose_secret_metadata(
    monkeypatch,
):
    monkeypatch.setattr(
        auth,
        "resolve_configured_agent_token",
        lambda config=None: "a" * 32,
    )
    service, _ = _service()
    app = Flask(__name__)
    app.extensions["project_access_authority"] = AllowProjectAccess()
    app.register_blueprint(
        create_source_control_git_authorizations_blueprint(service)
    )
    client = app.test_client()

    created = client.post(
        "/api/source-control/v1/git-authorizations",
        json=_selection(),
        headers=_headers(),
    )
    assert created.status_code == 200
    etag = created.headers["ETag"]
    detail = client.get(
        "/api/source-control/v1/git-authorizations/"
        "github-installation:42?repository=owner/repository",
        headers=_headers(),
    )
    revoked = client.post(
        "/api/source-control/v1/git-authorizations/"
        "github-installation:42/actions/revoke",
        json={"repository": "owner/repository"},
        headers={
            **_headers(),
            "Idempotency-Key": "git-auth:test-operation-0002",
            "If-Match": etag,
        },
    )

    assert detail.status_code == 200
    assert revoked.status_code == 200
    for response in (created, detail, revoked):
        encoded = response.get_data(as_text=True)
        assert "remote_url" not in encoded
        assert "credential_ref" not in encoded
        assert "vault://" not in encoded
        assert "github.com" not in encoded
