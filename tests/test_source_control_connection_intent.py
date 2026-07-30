from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from agent.services.hub_git_authorization_registry import (
    RegisteredGitAuthorization,
    ScopedGitAuthorizationRegistry,
)
from agent.services.source_control_catalogs import (
    ScopedRegisteredWorkspaceCatalog,
)
from agent.services.source_control_connection_intent import (
    SourceControlConnectionIntentError,
    SourceControlConnectionIntentResolver,
)
from agent.sources.git_source_connector_common import GitSourceScope
from agent.sources.registered_workspace_connector import RegisteredWorkspace


@dataclass(frozen=True)
class _Principal:
    subject_id: str = "owner-example"
    tenant_id: str = "tenant-example"
    project_id: str = "project-example"
    roles: frozenset[str] = frozenset({"project_owner"})


def test_workspace_identity_is_derived_from_scoped_catalog(
    tmp_path: Path,
) -> None:
    catalog = ScopedRegisteredWorkspaceCatalog(
        (
            RegisteredWorkspace(
                workspace_id="workspace-example",
                tenant_id="tenant-example",
                project_id="project-example",
                root=tmp_path,
                enabled=True,
                read_only=True,
            ),
        )
    )
    resolver = SourceControlConnectionIntentResolver(
        workspaces=catalog,
        remotes=ScopedGitAuthorizationRegistry(),
    )

    resolved = resolver.resolve(
        principal=_Principal(),
        payload={
            "connector_type": "registered_workspace",
            "workspace_id": "workspace-example",
            "display_name": "Workspace",
            "sensitivity": "internal",
            "dry_run": True,
        },
    )

    assert resolved.connector_type == "registered_workspace"
    assert len(resolved.connection_identity_digest) == 64


def test_remote_identity_and_connector_are_server_bound() -> None:
    record = RegisteredGitAuthorization(
        scope=GitSourceScope(
            tenant_id="tenant-example",
            project_id="project-example",
            owner_id="owner-example",
        ),
        connection_ref="remote-example",
        authorization_kind="github_app",
        remote_url="https://github.com/example/repository.git",
        credential_ref="vault:github/example",
        credential_username=None,
        authorization_state="active",
        granted_scopes=frozenset({"contents:read"}),
        repository="example/repository",
    )
    resolver = SourceControlConnectionIntentResolver(
        workspaces=ScopedRegisteredWorkspaceCatalog(),
        remotes=ScopedGitAuthorizationRegistry((record,)),
    )

    resolved = resolver.resolve(
        principal=_Principal(),
        payload={
            "connector_type": "github",
            "remote_id": "remote-example",
            "display_name": "Repository",
            "sensitivity": "internal",
            "dry_run": False,
        },
    )
    assert resolved.connector_type == "github"
    assert len(resolved.connection_identity_digest) == 64

    with pytest.raises(
        SourceControlConnectionIntentError,
        match="connector_mismatch",
    ):
        resolver.resolve(
            principal=_Principal(),
            payload={
                "connector_type": "git",
                "remote_id": "remote-example",
                "display_name": "Repository",
                "sensitivity": "internal",
                "dry_run": False,
            },
        )


def test_foreign_catalog_records_are_hidden(tmp_path: Path) -> None:
    resolver = SourceControlConnectionIntentResolver(
        workspaces=ScopedRegisteredWorkspaceCatalog(
            (
                RegisteredWorkspace(
                    workspace_id="workspace-example",
                    tenant_id="tenant-other",
                    project_id="project-example",
                    root=tmp_path,
                    enabled=True,
                    read_only=True,
                ),
            )
        ),
        remotes=ScopedGitAuthorizationRegistry(),
    )

    with pytest.raises(
        SourceControlConnectionIntentError,
        match="workspace_not_found",
    ):
        resolver.resolve(
            principal=_Principal(),
            payload={
                "connector_type": "registered_workspace",
                "workspace_id": "workspace-example",
                "display_name": "Workspace",
                "sensitivity": "internal",
                "dry_run": True,
            },
        )
