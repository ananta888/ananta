from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models.hub_git_authorization import (
    HubGitRemoteRegistrationAuditDB,
)
from agent.repositories.hub_git_authorization_repository import (
    HubGitAuthorizationPersistenceError,
    SQLHubGitAuthorizationRepository,
)
from agent.services.hub_git_authorization_registry import (
    RegisteredGitAuthorization,
)
from agent.sources.git_source_connector_common import GitSourceScope
from agent.sources.hub_git_persistent_composition import (
    compose_persistent_hub_git_source_connectors,
)

SCOPE = GitSourceScope(
    tenant_id="tenant-1",
    project_id="project-1",
    owner_id="owner-1",
)


def _session_factory() -> tuple[object, object]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine, lambda: Session(engine)


def _github(
    repository: str = "owner/repository",
    *,
    state: str = "active",
) -> RegisteredGitAuthorization:
    return RegisteredGitAuthorization(
        scope=SCOPE,
        connection_ref="github-installation:42",
        authorization_kind="github_app",
        remote_url=f"https://github.example/{repository}.git",
        credential_ref="vault:github-installation-42",
        credential_username="x-access-token",
        authorization_state=state,
        granted_scopes=frozenset({"contents:read"}),
        repository=repository,
    )


def _generic() -> RegisteredGitAuthorization:
    return RegisteredGitAuthorization(
        scope=SCOPE,
        connection_ref="generic-origin",
        authorization_kind="generic_git",
        remote_url="ssh://git@git.example/team/repository.git",
        credential_ref="vault:generic-origin-key",
        credential_username=None,
        authorization_state="active",
        granted_scopes=frozenset({"contents:read"}),
        repository=None,
    )


def test_registration_is_scoped_and_supports_installation_repository_fanout() -> None:
    _, factory = _session_factory()
    repository = SQLHubGitAuthorizationRepository(
        session_factory=factory,
        clock=lambda: 100,
    )

    assert repository.register(
        _github(),
        actor_id="operator-1",
        reason_code="connection_created",
    ) == 1
    assert repository.register(
        _github("owner/second"),
        actor_id="operator-1",
        reason_code="connection_created",
    ) == 1
    assert repository.register(
        _generic(),
        actor_id="operator-1",
        reason_code="connection_created",
    ) == 1

    assert repository.resolve_github(
        scope=SCOPE,
        authorization_ref="github-installation:42",
        repository="owner/repository",
    ) == _github()
    assert repository.resolve_github(
        scope=SCOPE,
        authorization_ref="github-installation:42",
        repository="owner/second",
    ) == _github("owner/second")
    assert repository.resolve_generic(
        scope=SCOPE,
        remote_id="generic-origin",
    ) == _generic()
    assert repository.resolve_github(
        scope=GitSourceScope(
            tenant_id="tenant-2",
            project_id="project-1",
            owner_id="owner-1",
        ),
        authorization_ref="github-installation:42",
        repository="owner/repository",
    ) is None


def test_github_oauth_registration_uses_the_same_scoped_contract() -> None:
    _, factory = _session_factory()
    repository = SQLHubGitAuthorizationRepository(session_factory=factory)
    oauth = replace(
        _github(),
        connection_ref="github-oauth:user-1",
        authorization_kind="github_oauth",
        credential_ref="vault:github-oauth-user-1",
    )

    repository.register(
        oauth,
        actor_id="operator-1",
        reason_code="oauth_connected",
    )

    assert repository.resolve_github(
        scope=SCOPE,
        authorization_ref="github-oauth:user-1",
        repository="owner/repository",
    ) == oauth


def test_provider_generated_repository_bound_reference_is_persistable() -> None:
    _, factory = _session_factory()
    repository = SQLHubGitAuthorizationRepository(session_factory=factory)
    oauth = replace(
        _github(),
        connection_ref="github-oauth:user-1",
        authorization_kind="github_oauth",
        credential_ref=(
            "secret://github-oauth/grant/user-1/repository/owner%2Frepository"
        ),
    )

    assert repository.register(
        oauth,
        actor_id="operator-1",
        reason_code="oauth_connected",
    ) == 1


def test_exact_registration_is_idempotent_but_changed_snapshot_conflicts() -> None:
    _, factory = _session_factory()
    repository = SQLHubGitAuthorizationRepository(session_factory=factory)
    record = _github()
    repository.register(
        record,
        actor_id="operator-1",
        reason_code="connection_created",
    )

    assert repository.register(
        record,
        actor_id="operator-1",
        reason_code="connection_retried",
    ) == 1
    changed = replace(
        record,
        remote_url="https://github.example/changed/repository.git",
    )
    with pytest.raises(
        HubGitAuthorizationPersistenceError,
        match="git_authorization_registration_conflict",
    ):
        repository.register(
            changed,
            actor_id="operator-1",
            reason_code="connection_changed",
        )


def test_revoke_uses_cas_and_preserves_immutable_revision_history() -> None:
    _, factory = _session_factory()
    repository = SQLHubGitAuthorizationRepository(
        session_factory=factory,
        clock=lambda: 100,
    )
    repository.register(
        _github(),
        actor_id="operator-1",
        reason_code="connection_created",
    )

    assert repository.transition_authorization_state(
        scope=SCOPE,
        connection_ref="github-installation:42",
        repository="owner/repository",
        authorization_state="revoked",
        expected_revision=1,
        actor_id="operator-2",
        reason_code="provider_revoked",
        granted_scopes=frozenset(),
    ) == 2
    assert repository.resolve_github(
        scope=SCOPE,
        authorization_ref="github-installation:42",
        repository="owner/repository",
    ).authorization_state == "revoked"
    history = repository.list_revisions(
        scope=SCOPE,
        connection_ref="github-installation:42",
        repository="owner/repository",
    )
    assert [item.authorization_state for item in history] == [
        "active",
        "revoked",
    ]
    assert history[0].granted_scopes_json == '["contents:read"]'
    assert history[1].granted_scopes_json == "[]"

    with pytest.raises(
        HubGitAuthorizationPersistenceError,
        match="git_authorization_revision_conflict",
    ):
        repository.transition_authorization_state(
            scope=SCOPE,
            connection_ref="github-installation:42",
            repository="owner/repository",
            authorization_state="scope_loss",
            expected_revision=1,
            actor_id="operator-3",
            reason_code="scope_refresh",
        )


def test_audit_schema_and_repr_do_not_expose_url_or_credential_reference() -> None:
    _, factory = _session_factory()
    repository = SQLHubGitAuthorizationRepository(session_factory=factory)
    record = _github()
    repository.register(
        record,
        actor_id="operator-1",
        reason_code="connection_created",
    )

    assert "remote_url" not in HubGitRemoteRegistrationAuditDB.__table__.columns
    assert (
        "credential_ref"
        not in HubGitRemoteRegistrationAuditDB.__table__.columns
    )
    assert record.remote_url not in repr(record)
    assert str(record.credential_ref) not in repr(record)
    with factory() as session:
        audit = session.exec(select(HubGitRemoteRegistrationAuditDB)).one()
    assert record.remote_url not in repr(audit)
    assert str(record.credential_ref) not in repr(audit)


def test_registration_rejects_credentials_embedded_in_remote_url() -> None:
    _, factory = _session_factory()
    repository = SQLHubGitAuthorizationRepository(session_factory=factory)
    unsafe = replace(
        _github(),
        remote_url="https://token@github.example/owner/repository.git",
    )

    with pytest.raises(
        HubGitAuthorizationPersistenceError,
        match="git_remote_embedded_credential_forbidden",
    ):
        repository.register(
            unsafe,
            actor_id="operator-1",
            reason_code="connection_created",
        )


def test_catalog_reads_are_strictly_tenant_project_and_owner_scoped() -> None:
    _, factory = _session_factory()
    repository = SQLHubGitAuthorizationRepository(session_factory=factory)
    record = _github()
    repository.register(
        record,
        actor_id="operator-1",
        reason_code="connection_created",
    )

    assert repository.list_authorizations(
        tenant_id=str(record.scope.tenant_id),
        project_id=str(record.scope.project_id),
        owner_id=str(record.scope.owner_id),
    ) == (record,)
    assert repository.list_authorizations(
        tenant_id=str(record.scope.tenant_id),
        project_id=str(record.scope.project_id),
        owner_id="foreign-owner",
    ) == ()
    assert repository.resolve_registered_remote(
        tenant_id=str(record.scope.tenant_id),
        project_id=str(record.scope.project_id),
        owner_id=str(record.scope.owner_id),
        remote_id=record.connection_ref,
    ) == record
    assert repository.resolve_registered_remote(
        tenant_id="foreign-tenant",
        project_id=str(record.scope.project_id),
        owner_id=str(record.scope.owner_id),
        remote_id=record.connection_ref,
    ) is None


class _NeverSecretResolver:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, reference: str) -> str:
        self.calls += 1
        raise AssertionError(reference)


class _RemotePolicy:
    def authorize(self, request: object) -> object:
        raise AssertionError(request)


def test_persistent_composition_is_lazy(
    tmp_path: Path,
) -> None:
    session_calls = 0
    secret_resolver = _NeverSecretResolver()

    def session_factory() -> Session:
        nonlocal session_calls
        session_calls += 1
        raise AssertionError("session must remain lazy")

    workspace_root = tmp_path / "workspaces"
    credential_root = tmp_path / "credentials"
    composition = compose_persistent_hub_git_source_connectors(
        session_factory=session_factory,
        config={
            "hub_git_workspace_root": workspace_root,
            "hub_git_credential_root": credential_root,
        },
        secret_resolver=secret_resolver,
        remote_policy=_RemotePolicy(),
    )

    assert composition.registry is not None
    assert composition.connectors.github_repository is not None
    assert composition.connectors.generic_git is not None
    assert session_calls == 0
    assert secret_resolver.calls == 0
    assert not workspace_root.exists()
    assert not credential_root.exists()
