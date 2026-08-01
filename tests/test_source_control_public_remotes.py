from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models.source_control_public_remote import (
    SourceControlPublicRemoteAuditDB,
    SourceControlPublicRemoteDB,
    SourceControlPublicRemoteValidationDB,
)
from agent.repositories.source_control_public_remote_repository import (
    SQLSourceControlPublicRemoteRepository,
)
from agent.services.git_remote_policy_service import GitRemoteAccessPolicy
from agent.services.source_control_public_remote_contracts import (
    PublicRemoteSelection,
    SourceControlPublicRemoteContractError,
)
from agent.services.source_control_public_remote_service import (
    SourceControlPublicRemoteError,
    SourceControlPublicRemoteService,
)
from agent.routes.source_control_public_remotes import (
    create_source_control_public_remotes_blueprint,
)
from agent.services.user_session_tokens import issue_user_access_token

_COMMIT = "a" * 40
_PUBLIC_IP = "93.184.216.34"


class _Transport:
    def __init__(self) -> None:
        self.authorizations = []

    def supports_authorization(self, authorization) -> bool:
        self.authorizations.append(authorization)
        return True

    def resolve_commit(
        self,
        *,
        authorization,
        credential_username,
        requested_ref,
    ) -> str:
        assert authorization.scheme == "https"
        assert authorization.port == 443
        assert authorization.credential_ref is None
        assert credential_username is None
        assert requested_ref
        return _COMMIT


class _Idempotency:
    def __init__(self) -> None:
        self.completed = {}

    def claim(self, *, idempotency_key: str, plan_digest: str):
        key = (idempotency_key, plan_digest)
        if key in self.completed:
            return SimpleNamespace(
                state="completed",
                claim_token=None,
                result=self.completed[key],
            )
        return SimpleNamespace(
            state="claimed",
            claim_token="claim-example",
            result=None,
        )

    def complete(
        self,
        *,
        idempotency_key,
        plan_digest,
        claim_token,
        result,
    ) -> None:
        assert claim_token == "claim-example"
        self.completed[(idempotency_key, plan_digest)] = dict(result)


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _principal():
    return SimpleNamespace(
        tenant_id="tenant-example",
        project_id="project-example",
        subject_id="owner-example",
        roles=frozenset({"project_owner"}),
    )


def _service(*, now, enabled=True):
    engine = _engine()
    repository = SQLSourceControlPublicRemoteRepository(
        session_factory=lambda: Session(engine),
        clock=lambda: now[0],
    )
    transport = _Transport()
    service = SourceControlPublicRemoteService(
        repository=repository,
        remote_policy=GitRemoteAccessPolicy(
            allowed_schemes=("https",),
            allowed_hosts=("github.com", "git.example.com"),
            dns_resolver=lambda _host, _port: (_PUBLIC_IP,),
        ),
        transport=transport,
        idempotency=_Idempotency(),
        enabled=enabled,
        connector_registry_ready=True,
        ttl_seconds=60,
        clock=lambda: now[0],
        token_factory=lambda: "x" * 43,
    )
    return engine, repository, transport, service


@pytest.mark.parametrize(
    "payload",
    (
        {
            "provider": "github_public",
            "owner": "example",
            "repository": "repo",
            "requested_ref": "HEAD",
            "remote_url": "https://attacker.invalid/repo",
        },
        {
            "provider": "https_git",
            "host": "127.0.0.1",
            "repository": "example/repo.git",
            "requested_ref": "main",
        },
        {
            "provider": "https_git",
            "host": "git.example.com:8443",
            "repository": "example/repo.git",
            "requested_ref": "main",
        },
        {
            "provider": "https_git",
            "host": "git.example.com",
            "repository": "example/repo.git",
            "requested_ref": "main",
            "credential": "secret",
        },
    ),
)
def test_contract_rejects_urls_credentials_ports_and_ip_literals(
    payload,
) -> None:
    with pytest.raises(SourceControlPublicRemoteContractError):
        PublicRemoteSelection.from_mapping(payload)


def test_validate_and_create_persist_only_hashed_handle_and_opaque_id() -> None:
    now = [1_000.0]
    engine, repository, transport, service = _service(now=now)

    validation = service.validate(
        principal=_principal(),
        payload={
            "provider": "github_public",
            "owner": "example",
            "repository": "repo",
            "requested_ref": "HEAD",
        },
    )
    created = service.create(
        principal=_principal(),
        payload={
            "validation_handle": validation["validation_handle"],
        },
        idempotency_key="public-remote-example",
    )
    replay = service.create(
        principal=_principal(),
        payload={
            "validation_handle": validation["validation_handle"],
        },
        idempotency_key="public-remote-example",
    )

    assert created == replay
    assert created["remote_id"].startswith("pub_")
    assert created["commit_sha"] == _COMMIT
    assert "host" not in created
    assert "repository" not in created
    assert "remote_url" not in created
    authorization = repository.resolve_registered_remote(
        tenant_id="tenant-example",
        project_id="project-example",
        owner_id="owner-example",
        remote_id=created["remote_id"],
    )
    assert authorization.authorization_kind == "github_public"
    assert authorization.credential_ref is None
    assert transport.authorizations[0].redirects == "deny"
    assert transport.authorizations[0].proxy == "deny"
    assert transport.authorizations[0].recurse_submodules is False
    assert transport.authorizations[0].lfs_mode == "disabled"

    with Session(engine) as session:
        handle = session.exec(
            select(SourceControlPublicRemoteValidationDB)
        ).one()
        remote = session.exec(select(SourceControlPublicRemoteDB)).one()
        audits = session.exec(
            select(SourceControlPublicRemoteAuditDB)
        ).all()
    assert handle.handle_digest != validation["validation_handle"]
    assert handle.remote_id == created["remote_id"]
    assert not hasattr(remote, "remote_url")
    assert not hasattr(remote, "credential_ref")
    assert {item.reason_code for item in audits} == {
        "public_remote_validated",
        "public_remote_created",
    }


def test_expired_handle_is_rejected_and_audited() -> None:
    now = [1_000.0]
    engine, _repository, _transport, service = _service(now=now)
    validation = service.validate(
        principal=_principal(),
        payload={
            "provider": "https_git",
            "host": "git.example.com",
            "repository": "example/repo.git",
            "requested_ref": "main",
        },
    )
    now[0] = 1_061.0

    with pytest.raises(
        SourceControlPublicRemoteError,
        match="public_remote_validation_expired",
    ):
        service.create(
            principal=_principal(),
            payload={
                "validation_handle": validation["validation_handle"],
            },
            idempotency_key="public-remote-expiry",
        )

    with Session(engine) as session:
        audits = session.exec(
            select(SourceControlPublicRemoteAuditDB)
        ).all()
    assert any(
        item.decision == "deny"
        and item.reason_code == "public_remote_validation_expired"
        for item in audits
    )


def test_feature_flag_defaults_to_fail_closed_service_behavior() -> None:
    now = [1_000.0]
    _engine_value, _repository, _transport, service = _service(
        now=now,
        enabled=False,
    )

    with pytest.raises(
        SourceControlPublicRemoteError,
        match="public_remote_feature_disabled",
    ):
        service.validate(
            principal=_principal(),
            payload={
                "provider": "github_public",
                "owner": "example",
                "repository": "repo",
                "requested_ref": "HEAD",
            },
        )


class _PublicRemoteRouteService:
    def __init__(self) -> None:
        self.principal = None
        self.payload = None

    def validate(self, *, principal, payload):
        self.principal = principal
        self.payload = dict(payload)
        return {"available": True}


def test_public_remote_route_binds_normal_admin_token_project_selector() -> None:
    app = Flask(__name__)
    app.config["TESTING"] = True
    service = _PublicRemoteRouteService()
    app.register_blueprint(
        create_source_control_public_remotes_blueprint(service)
    )
    token = issue_user_access_token(username="admin", role="admin")

    response = app.test_client().post(
        "/api/source-control/v1/public-remotes/validate"
        "?project_id=project-example",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "provider": "github_public",
            "owner": "example",
            "repository": "repository",
            "requested_ref": "HEAD",
        },
    )

    assert response.status_code == 200
    assert service.principal.tenant_id
    assert service.principal.subject_id == "admin"
    assert service.principal.project_id == "project-example"
    assert set(service.payload) == {
        "provider",
        "owner",
        "repository",
        "requested_ref",
    }


def test_public_remote_route_requires_project_selector() -> None:
    app = Flask(__name__)
    app.config["TESTING"] = True
    service = _PublicRemoteRouteService()
    app.register_blueprint(
        create_source_control_public_remotes_blueprint(service)
    )
    token = issue_user_access_token(username="admin", role="admin")

    response = app.test_client().post(
        "/api/source-control/v1/public-remotes/validate",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "project_id_required"
    assert service.principal is None
