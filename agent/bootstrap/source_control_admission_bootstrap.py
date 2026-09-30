"""Source admission composition (budgets, revision coordinator, scan router).
"""

from __future__ import annotations

from sqlalchemy.engine import Engine

from agent.repositories.source_admission_receipt_repository import (
    SQLSourceAdmissionReceiptRepository,
)
from agent.repositories.source_control_repository import (
    SQLSourceControlRepository,
)
from agent.services.registered_workspace_source_admission import (
    RegisteredWorkspaceSourceAdmissionService,
)
from agent.services.remote_git_source_admission import (
    RemoteGitSourceAdmissionService,
    SourceScanServiceRouter,
)
from agent.services.source_admission_revision_coordinator import (
    SourceAdmissionRevisionCoordinator,
)
from agent.services.source_admission_service import SourceAdmissionBudgets
from agent.sources.source_control_connector_composition import (
    build_source_control_connector_extensions,
)
from agent.sources.source_refresh_service import SourceRefreshService


def compose_source_admission(
    app,
    *,
    db_engine: Engine,
    workspace_catalog: object,
    workspace_source_connector: object,
    source_scanner: object,
    registered_remote_catalog: object,
    remote_payload_store: object,
    refresh: SourceRefreshService,
) -> SourceAdmissionBudgets:
    """Compose admission services and register workspace connectors."""


    source_admission_budgets = app.extensions.get(
        "source_admission_budgets"
    )
    if not isinstance(source_admission_budgets, SourceAdmissionBudgets):
        source_admission_budgets = SourceAdmissionBudgets()
        app.extensions[
            "source_admission_budgets"
        ] = source_admission_budgets
    if app.extensions.get("source_admission_revision_coordinator") is None:
        app.extensions[
            "source_admission_revision_coordinator"
        ] = SourceAdmissionRevisionCoordinator(
            scanner=source_scanner,
            revision_repository=SQLSourceControlRepository(db_engine),
            receipt_repository=SQLSourceAdmissionReceiptRepository(db_engine),
            budgets=source_admission_budgets,
        )
    if app.extensions.get("source_scan_service") is None:
        workspace_scan_service = RegisteredWorkspaceSourceAdmissionService(
            engine=db_engine,
            workspace_catalog=workspace_catalog,
            workspace_connector=workspace_source_connector,
            coordinator=app.extensions[
                "source_admission_revision_coordinator"
            ],
            budgets=source_admission_budgets,
        )
        remote_scan_service = RemoteGitSourceAdmissionService(
            engine=db_engine,
            registry=registered_remote_catalog,
            payload_store=remote_payload_store,
            revision_repository=SQLSourceControlRepository(db_engine),
            receipt_repository=SQLSourceAdmissionReceiptRepository(db_engine),
            budgets=source_admission_budgets,
        )
        app.extensions["source_scan_service"] = SourceScanServiceRouter(
            {
                "registered_workspace": workspace_scan_service,
                "local_directory": workspace_scan_service,
                "generic_git": remote_scan_service,
                "github_repository": remote_scan_service,
            }
        )
    registered_types = frozenset(refresh.connector_registry.list_types())
    for connector in build_source_control_connector_extensions(
        registered_workspace=workspace_source_connector
    ):
        if connector.connector_type not in registered_types:
            refresh.connector_registry.register(connector)
            registered_types = registered_types | {connector.connector_type}
    return source_admission_budgets


__all__ = ["compose_source_admission"]
