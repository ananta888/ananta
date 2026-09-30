"""Workspace catalogs, registrations and snapshot upload composition.

Composes the registry-backed and persistent registered-workspace catalogs,
the registration service, the workspace source connector, the filesystem
scanner and the browser-folder snapshot upload service, reusing any
instance already present in ``app.extensions``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy.engine import Engine
from sqlmodel import Session

from agent.repositories.source_control_workspace_registration_repository import (
    SQLSourceControlWorkspaceRegistrationRepository,
)
from agent.services.ops_registry_service import get_ops_registry_service
from agent.services.source_control_api_runtime import (
    SQLSourceControlOperationStore,
)
from agent.services.source_control_catalogs import (
    SourceRegistryRegisteredWorkspaceCatalog,
)
from agent.services.source_control_workspace_catalog import (
    CompositeRegisteredWorkspaceCatalog,
    SecureWorkspaceFolderCatalog,
    SQLRegisteredWorkspaceCatalog,
)
from agent.services.source_control_workspace_registration_service import (
    SourceControlWorkspaceRegistrationService,
)
from agent.services.source_control_workspace_snapshot_service import (
    WorkspaceSnapshotUploadService,
)
from agent.services.source_filesystem_scanner import (
    ProductionFilesystemSourceScanner,
)
from agent.sources.source_registry import SourceRegistry


@dataclass(frozen=True)
class SourceControlWorkspaceComposition:
    workspace_catalog: CompositeRegisteredWorkspaceCatalog
    workspace_registrations: SourceControlWorkspaceRegistrationService
    workspace_source_connector: object
    source_scanner: object
    workspace_snapshot_upload: object


def compose_source_control_workspaces(
    app,
    *,
    db_engine: Engine,
    registry: SourceRegistry,
    hub_workspace_root: object,
) -> SourceControlWorkspaceComposition:
    """Compose and register the workspace side of Source Control."""


    base_workspace_catalog = app.extensions.get(
        "registered_workspace_catalog"
    )
    if base_workspace_catalog is None:
        base_workspace_catalog = SourceRegistryRegisteredWorkspaceCatalog(
            registry=registry,
            registrations=get_ops_registry_service(),
        )
    workspace_registration_repository = (
        app.extensions.get(
            "source_control_workspace_registration_repository"
        )
        or SQLSourceControlWorkspaceRegistrationRepository(
            session_factory=lambda: Session(db_engine)
        )
    )
    workspace_folders = (
        app.extensions.get("source_control_workspace_folder_catalog")
        or SecureWorkspaceFolderCatalog(
            workspace_root=app.config.get(
                "ANANTA_WORKSPACE_ROOT",
                os.environ.get("ANANTA_WORKSPACE_ROOT"),
            )
        )
    )
    persistent_workspace_catalog = SQLRegisteredWorkspaceCatalog(
        repository=workspace_registration_repository,
        folders=workspace_folders,
    )
    workspace_catalog = CompositeRegisteredWorkspaceCatalog(
        (base_workspace_catalog, persistent_workspace_catalog)
    )
    app.extensions[
        "source_control_workspace_registration_repository"
    ] = workspace_registration_repository
    app.extensions[
        "source_control_workspace_folder_catalog"
    ] = workspace_folders
    app.extensions[
        "source_control_persistent_workspace_catalog"
    ] = persistent_workspace_catalog
    app.extensions[
        "registered_workspace_catalog"
    ] = workspace_catalog
    workspace_registrations = SourceControlWorkspaceRegistrationService(
        repository=workspace_registration_repository,
        folders=workspace_folders,
        idempotency=SQLSourceControlOperationStore(db_engine),
        project_access=app.extensions["project_access_authority"],
    )
    app.extensions[
        "source_control_workspace_registration_service"
    ] = workspace_registrations
    workspace_source_connector = app.extensions.get(
        "registered_workspace_source_connector"
    )
    if workspace_source_connector is None:
        from agent.sources.registered_workspace_connector import (
            RegisteredWorkspaceConnector,
        )

        workspace_source_connector = RegisteredWorkspaceConnector(
            catalog=workspace_catalog
        )
        app.extensions[
            "registered_workspace_source_connector"
        ] = workspace_source_connector
    source_scanner = app.extensions.get("source_filesystem_scanner")
    if source_scanner is None:
        source_scanner = ProductionFilesystemSourceScanner()
        app.extensions["source_filesystem_scanner"] = source_scanner
    workspace_snapshot_upload = app.extensions.get(
        "source_control_workspace_snapshot_upload_service"
    )
    if workspace_snapshot_upload is None:
        workspace_snapshot_upload = WorkspaceSnapshotUploadService(
            workspace_root=hub_workspace_root,
            project_access=app.extensions["project_access_authority"],
            folders=workspace_folders,
            workspace_registrations=workspace_registrations,
            idempotency=SQLSourceControlOperationStore(db_engine),
            scanner=source_scanner,
        )
        app.extensions[
            "source_control_workspace_snapshot_upload_service"
        ] = workspace_snapshot_upload
    return SourceControlWorkspaceComposition(
        workspace_catalog=workspace_catalog,
        workspace_registrations=workspace_registrations,
        workspace_source_connector=workspace_source_connector,
        source_scanner=source_scanner,
        workspace_snapshot_upload=workspace_snapshot_upload,
    )


__all__ = [
    "SourceControlWorkspaceComposition",
    "compose_source_control_workspaces",
]
