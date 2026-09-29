from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy.dialects import postgresql
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlmodel import Session, SQLModel, create_engine

import agent.db_models
from agent.repositories.source_control_workspace_registration_repository import (
    SQLSourceControlWorkspaceRegistrationRepository,
)
from agent.services.source_control_workspace_catalog import (
    SecureWorkspaceFolderCatalog,
    SourceControlWorkspaceCatalogError,
)
from agent.services.source_control_workspace_registration_service import (
    SourceControlWorkspaceRegistrationService,
)
from agent.routes.source_control_workspace_registrations import (
    create_source_control_workspace_registrations_blueprint,
)
from agent.services.user_session_tokens import issue_user_access_token
from agent.services.project_access_authority import (
    AuthorizedProjectScope,
    ProjectCapability,
)


class _Idempotency:
    def __init__(self) -> None:
        self.results = {}

    def claim(self, *, idempotency_key, plan_digest):
        result = self.results.get((idempotency_key, plan_digest))
        return SimpleNamespace(
            state="completed" if result is not None else "claimed",
            claim_token=None if result is not None else "claim",
            result=result,
        )

    def complete(
        self,
        *,
        idempotency_key,
        plan_digest,
        claim_token,
        result,
    ):
        assert claim_token == "claim"
        self.results[(idempotency_key, plan_digest)] = dict(result)


class _ProjectAccess:
    def require(self, **kwargs):
        return AuthorizedProjectScope(
            tenant_id=kwargs["tenant_id"],
            project_id=kwargs["project_id"],
            team_id=kwargs["project_id"],
            subject_id=kwargs["subject_id"],
            role="owner",
            status="active",
            capability=kwargs.get("capability", ProjectCapability.READ),
            lock_version=1,
        )


def _project_root(root: Path) -> Path:
    tenant = hashlib.sha256(b"tenant-example").hexdigest()
    project = hashlib.sha256(b"project-example").hexdigest()
    scoped = root / tenant / project
    scoped.mkdir(parents=True)
    return scoped


def _principal():
    return SimpleNamespace(
        tenant_id="tenant-example",
        project_id="project-example",
        subject_id="owner-example",
        roles=frozenset({"project_owner"}),
    )


def _service(tmp_path: Path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    now = [1_000.0]
    repository = SQLSourceControlWorkspaceRegistrationRepository(
        session_factory=lambda: Session(engine),
        clock=lambda: now[0],
    )
    folders = SecureWorkspaceFolderCatalog(workspace_root=tmp_path)
    service = SourceControlWorkspaceRegistrationService(
        repository=repository,
        folders=folders,
        idempotency=_Idempotency(),
        project_access=_ProjectAccess(),
        ttl_seconds=60,
        clock=lambda: now[0],
        token_factory=lambda: "x" * 43,
    )
    return now, repository, folders, service


def test_list_validate_create_and_disable_never_project_paths(
    tmp_path: Path,
) -> None:
    folder = _project_root(tmp_path) / "private-project-name"
    folder.mkdir()
    (folder / "secret-filename.txt").write_text("content", encoding="utf-8")
    _now, _repository, _folders, service = _service(tmp_path)

    listing = service.list_folders(principal=_principal())
    rendered = repr(listing)
    assert listing["items"][0]["display_name"] == "private-project-name"
    assert str(folder.parent) not in rendered
    assert "secret-filename.txt" not in rendered
    folder_handle = listing["items"][0]["folder_handle"]
    validation = service.validate(
        principal=_principal(),
        payload={"folder_handle": folder_handle},
    )
    created = service.create(
        principal=_principal(),
        payload={"validation_handle": validation["validation_handle"]},
        idempotency_key="workspace-create-example",
    )
    replay = service.create(
        principal=_principal(),
        payload={"validation_handle": validation["validation_handle"]},
        idempotency_key="workspace-create-example",
    )
    assert created == replay
    assert created["workspace_id"].startswith("ws_")
    assert created["read_only"] is True

    disabled = service.disable(
        principal=_principal(),
        workspace_id=created["workspace_id"],
        expected_revision=1,
        idempotency_key="workspace-disable-example",
    )
    assert disabled["state"] == "disabled"
    assert disabled["etag"] == '"workspace-v1:2"'


def test_symlink_in_workspace_is_rejected(tmp_path: Path) -> None:
    folder = _project_root(tmp_path) / "workspace"
    folder.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("outside", encoding="utf-8")
    (folder / "escape").symlink_to(outside)
    catalog = SecureWorkspaceFolderCatalog(workspace_root=tmp_path)

    with pytest.raises(
        SourceControlWorkspaceCatalogError,
        match="workspace_symlink_denied",
    ):
        catalog.list_folders(
            tenant_id="tenant-example",
            project_id="project-example",
        )


def test_manifest_change_invalidates_validate_then_create(
    tmp_path: Path,
) -> None:
    folder = _project_root(tmp_path) / "workspace"
    folder.mkdir()
    source = folder / "source.txt"
    source.write_text("before", encoding="utf-8")
    _now, _repository, _folders, service = _service(tmp_path)
    folder_handle = service.list_folders(
        principal=_principal()
    )["items"][0]["folder_handle"]
    validation = service.validate(
        principal=_principal(),
        payload={"folder_handle": folder_handle},
    )
    source.write_text("after", encoding="utf-8")

    with pytest.raises(Exception, match="revalidation"):
        service.create(
            principal=_principal(),
            payload={"validation_handle": validation["validation_handle"]},
            idempotency_key="workspace-revalidation-example",
        )


def test_new_source_control_schema_identifiers_fit_postgres() -> None:
    prefixes = (
        "source_connection_selectors",
        "source_control_public_remote",
        "source_control_workspace_",
    )
    tables = tuple(
        table
        for table in SQLModel.metadata.sorted_tables
        if table.name.startswith(prefixes)
    )
    names = [
        item.name
        for table in tables
        for item in (*table.indexes, *table.constraints)
        if item.name
    ]

    assert names
    assert max(map(len, names)) <= 63
    assert "ix_sc_workspace_reg_validation" in names
    assert "uq_sc_workspace_reg_validation" in names
    dialect = postgresql.dialect()
    for table in tables:
        str(CreateTable(table).compile(dialect=dialect))
        for index in table.indexes:
            str(CreateIndex(index).compile(dialect=dialect))


class _WorkspaceRouteService:
    def __init__(self) -> None:
        self.principal = None

    def list_folders(self, *, principal):
        self.principal = principal
        return {"items": []}


def test_workspace_route_binds_normal_admin_token_project_selector() -> None:
    app = Flask(__name__)
    app.extensions["project_access_authority"] = _ProjectAccess()
    app.config["TESTING"] = True
    service = _WorkspaceRouteService()
    app.register_blueprint(
        create_source_control_workspace_registrations_blueprint(service)
    )
    token = issue_user_access_token(username="admin", role="admin")

    response = app.test_client().get(
        "/api/source-control/v1/workspace-folders"
        "?project_id=project-example",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert service.principal.tenant_id
    assert service.principal.subject_id == "admin"
    assert service.principal.project_id == "project-example"


def test_workspace_route_authorizes_member_selector_without_project_claim() -> None:
    app = Flask(__name__)
    app.extensions["project_access_authority"] = _ProjectAccess()
    app.config["TESTING"] = True
    service = _WorkspaceRouteService()
    app.register_blueprint(
        create_source_control_workspace_registrations_blueprint(service)
    )
    token = issue_user_access_token(
        username="project-owner",
        role="project_owner",
    )

    response = app.test_client().get(
        "/api/source-control/v1/workspace-folders"
        "?project_id=project-example",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert service.principal.tenant_id
    assert service.principal.subject_id == "project-owner"
    assert service.principal.project_id == "project-example"
    assert service.principal.roles == frozenset({"project_owner"})
