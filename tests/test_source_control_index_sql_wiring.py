from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from flask import Flask
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from agent.db_models.source_access_enforcement import (
    SourceAccessGrantExecutionPolicyDB,
)
from agent.db_models.source_admission_receipt import SourceAdmissionReceiptDB
from agent.db_models.source_control import (
    SourceAccessGrantDB,
    SourceConnectionDB,
    SourceConnectionSelectorDB,
    SourceRevisionDB,
)
from agent.services.knowledge_index_execution_binding_service import (
    KnowledgeIndexExecutionBindingService,
)
from agent.services.knowledge_index_job_service import KnowledgeIndexJobService
from agent.services.service_registry import initialize_core_services
from agent.services.source_access_enforcement import source_access_grant_digest
from agent.services.source_access_manifest_signing import SourceAccessSigningKey
from agent.services.source_admission_service import SourceAdmissionBudgets
from agent.services.source_control_index_authority_planner import (
    BoundSourceRevisionAuthority,
    BoundSourceRevisionAuthorityPlanner,
)
from agent.services.source_control_index_production_wiring import (
    RegisteredWorkspaceBoundSourcePayloadAdapter,
    SQLCurrentKnowledgeIndexAuthorityAdapter,
    build_source_control_index_production_composition,
)
from agent.services.source_destination_resolution import DestinationCatalogRecord
from agent.services.source_filesystem_scanner import ProductionFilesystemSourceScanner
from agent.sources.registered_workspace_connector import (
    RegisteredWorkspace,
    RegisteredWorkspaceConnector,
)
from ananta_contracts.source_control import ProviderLocation, SourceAccessGrant


def _current_time() -> datetime:
    """Resolve time at test execution, not during multi-hour collection."""

    return datetime.now(timezone.utc)


def _engine():
    database = create_engine("sqlite+pysqlite:///:memory:")
    SQLModel.metadata.create_all(database)
    return database


def _connection_values(connection_id: str) -> dict[str, object]:
    return {
        "connection_id": connection_id,
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "owner_id": "owner-1",
        "connector_type": "registered_workspace",
        "connection_identity_digest": "1" * 64,
        "display_name": "Workspace",
        "sensitivity": "internal",
        "state": "active",
        "lock_version": 1,
        "created_at_epoch": _current_time().timestamp(),
        "updated_at_epoch": _current_time().timestamp(),
    }


def test_current_execution_authority_is_rebuilt_from_exact_sql_state() -> None:
    database = _engine()
    connection_id = "conn_" + "a" * 64
    revision_id = "srev_" + "b" * 64
    destination_id = "dst_" + "c" * 64
    grant = SourceAccessGrant.create(
        version=1,
        tenant_id="tenant-1",
        project_id="project-1",
        source_revision_id=revision_id,
        destination_id=destination_id,
        operation="index",
        transformation="redacted",
        purpose="knowledge-index",
        policy_version="policy-v1",
        policy_snapshot_digest="d" * 64,
        state="active",
        issued_at=_current_time() - timedelta(minutes=1),
        expires_at=_current_time() + timedelta(hours=1),
    )
    with Session(database) as session:
        session.add(SourceConnectionDB(**_connection_values(connection_id)))
        session.add(
            SourceRevisionDB(
                source_revision_id=revision_id,
                connection_id=connection_id,
                tenant_id="tenant-1",
                project_id="project-1",
                owner_id="owner-1",
                connector_type="registered_workspace",
                sensitivity="internal",
                revision_token="workspace-manifest:" + "e" * 64,
                revision_digest="f" * 64,
                content_manifest_id="manifest_" + "e" * 64,
                content_manifest_digest="e" * 64,
                admission_state="admitted",
                captured_at_epoch=_current_time().timestamp(),
            )
        )
        session.add(
            SourceAdmissionReceiptDB(
                receipt_id="sar_" + "9" * 64,
                tenant_id="tenant-1",
                project_id="project-1",
                source_revision_id=revision_id,
                decision_state="admitted",
                reason_codes=[],
                revision_digest="f" * 64,
                manifest_digest="e" * 64,
                policy_digest="8" * 64,
                inventory_evidence_digest="7" * 64,
                scan_evidence_digest="6" * 64,
                admission_digest="9" * 64,
                file_count=1,
                total_bytes=4,
                largest_file_bytes=4,
                archive_expansion_ratio=0.0,
                symlink_count=0,
                hardlink_count=0,
                sparse_file_count=0,
                archive_count=0,
                binary_count=0,
                secret_findings=0,
                injection_findings=0,
                rejected_type_findings=0,
                malformed_archive_findings=0,
                scan_error_count=0,
                evaluated_at_epoch=_current_time().timestamp(),
                persisted_at_epoch=_current_time().timestamp(),
            )
        )
        session.add(
            SourceAccessGrantDB(
                grant_id=grant.grant_id,
                grant_family_id="family-1",
                grant_version=grant.version,
                tenant_id=grant.tenant_id,
                project_id=grant.project_id,
                owner_id="owner-1",
                source_revision_id=grant.source_revision_id,
                destination_id=grant.destination_id,
                operation=grant.operation.value,
                transformation=grant.transformation.value,
                purpose=grant.purpose,
                policy_version=grant.policy_version,
                policy_snapshot_digest=grant.policy_snapshot_digest,
                state=grant.state.value,
                issued_at_epoch=grant.issued_at.timestamp(),
                expires_at_epoch=grant.expires_at.timestamp(),
                lock_version=1,
                updated_at_epoch=_current_time().timestamp(),
            )
        )
        session.add(
            SourceAccessGrantExecutionPolicyDB(
                grant_id=grant.grant_id,
                grant_digest=source_access_grant_digest(grant),
                destination_digest="5" * 64,
                consumption_mode="one_time",
                grant_lock_version=1,
                concurrency_version=1,
                created_at=_current_time(),
                updated_at=_current_time(),
            )
        )
        session.commit()

    authority = SQLCurrentKnowledgeIndexAuthorityAdapter(database).resolve(
        tenant_id="tenant-1",
        project_id="project-1",
        source_revision_id=revision_id,
        destination_id=destination_id,
        source_access_grant_id=grant.grant_id,
    )

    assert authority is not None
    assert authority.admission_digest == "9" * 64
    assert authority.destination_digest == "5" * 64
    assert authority.source_access_grant_digest == source_access_grant_digest(grant)


class _WorkspaceCatalog:
    def __init__(self, workspace: RegisteredWorkspace) -> None:
        self.workspace = workspace

    def get(self, **scope: object) -> RegisteredWorkspace | None:
        if scope["workspace_id"] != self.workspace.workspace_id:
            return None
        return self.workspace


def test_workspace_payload_adapter_reopens_only_the_exact_manifest(
    tmp_path: Path,
) -> None:
    (tmp_path / "source.txt").write_text("safe source", encoding="utf-8")
    workspace = RegisteredWorkspace(
        workspace_id="workspace-1",
        tenant_id="tenant-1",
        project_id="project-1",
        owner_id="owner-1",
        root=tmp_path,
        enabled=True,
        read_only=True,
    )
    catalog = _WorkspaceCatalog(workspace)
    connector = RegisteredWorkspaceConnector(catalog=catalog)
    snapshot = connector.inventory(
        tenant_id="tenant-1",
        project_id="project-1",
        workspace_id="workspace-1",
    )
    database = _engine()
    connection_id = "conn_" + "1" * 64
    revision_id = "srev_" + "2" * 64
    with Session(database) as session:
        session.add(SourceConnectionDB(**_connection_values(connection_id)))
        session.add(
            SourceConnectionSelectorDB(
                connection_id=connection_id,
                tenant_id="tenant-1",
                project_id="project-1",
                owner_id="owner-1",
                public_connector_type="registered_workspace",
                implementation_connector_type="registered_workspace",
                selector_kind="workspace",
                selector_id="workspace-1",
                relative_path=".",
                binding_digest="3" * 64,
                created_at_epoch=_current_time().timestamp(),
            )
        )
        session.add(
            SourceRevisionDB(
                source_revision_id=revision_id,
                connection_id=connection_id,
                tenant_id="tenant-1",
                project_id="project-1",
                owner_id="owner-1",
                connector_type="registered_workspace",
                sensitivity="internal",
                revision_token="workspace-manifest:" + snapshot.manifest_digest,
                revision_digest=snapshot.revision_digest,
                content_manifest_id="manifest_" + snapshot.manifest_digest,
                content_manifest_digest=snapshot.manifest_digest,
                admission_state="admitted",
                captured_at_epoch=_current_time().timestamp(),
            )
        )
        session.commit()
    authority = BoundSourceRevisionAuthority(
        tenant_id="tenant-1",
        project_id="project-1",
        connection_id=connection_id,
        source_revision_id=revision_id,
        source_revision_digest=snapshot.revision_digest,
        content_manifest_digest=snapshot.manifest_digest,
        connector_type="registered_workspace",
        source_id=f"source-control:{connection_id}",
        admission_state="admitted",
        admission_digest="4" * 64,
    )
    adapter = RegisteredWorkspaceBoundSourcePayloadAdapter(
        engine=database,
        workspace_catalog=catalog,
        workspace_connector=connector,
        scanner=ProductionFilesystemSourceScanner(),
        budgets=SourceAdmissionBudgets(
            allowed_file_types=frozenset({"txt"})
        ),
    )

    payload = adapter.load_bound_revision_payload(authority)

    assert payload.source_revision_digest == snapshot.revision_digest
    assert payload.files[0]["relative_path"] == "source.txt"
    assert payload.records[0]["content"] == "safe source"


class _DestinationCatalog:
    def resolve(self, **selection: str) -> DestinationCatalogRecord:
        return DestinationCatalogRecord(
            worker_id=selection["worker_id"],
            worker_kind="retrieval",
            runtime_id=selection["runtime_id"],
            runtime_kind="worker",
            provider_id=selection["provider_id"],
            model_id=selection["model_id"],
            model_class="embedding",
            provider_location=ProviderLocation.LOCAL_CONTAINER,
            data_residency="local",
            enabled=True,
            authorization_status="authorized",
        )


class _Queue:
    def ingest_task(self, **values: object) -> None:
        del values


class _TaskRepository:
    def get_by_id(self, task_id: str):
        del task_id
        return None

    def save(self, task: object):
        return task


class _Ingestion:
    def upload_artifact(self, **values: object):
        del values
        raise AssertionError("composition must not persist during construction")


@dataclass(frozen=True)
class _KnowledgeServices:
    knowledge_index_job_service: object


@dataclass(frozen=True)
class _CoreServices:
    knowledge: _KnowledgeServices
    task_queue_service: object
    ingestion_service: object


def test_production_composition_job_service_survives_late_registry_rebuild(
    monkeypatch,
) -> None:
    app = Flask(__name__)
    original = object()
    app.extensions["core_services"] = _CoreServices(
        knowledge=_KnowledgeServices(original),
        task_queue_service=_Queue(),
        ingestion_service=_Ingestion(),
    )
    app.extensions["repository_registry"] = SimpleNamespace(
        agent_repo=SimpleNamespace(get_all=lambda: []),
        artifact_repo=SimpleNamespace(save=lambda value: value),
        task_repo=_TaskRepository(),
        knowledge_index_repo=object(),
        knowledge_index_run_repo=object(),
    )
    workspace = RegisteredWorkspace(
        workspace_id="workspace-1",
        tenant_id="tenant-1",
        project_id="project-1",
        owner_id="owner-1",
        root=Path("."),
        enabled=True,
        read_only=True,
    )
    catalog = _WorkspaceCatalog(workspace)
    destination_catalog = _DestinationCatalog()

    composition = build_source_control_index_production_composition(
        app=app,
        engine=_engine(),
        destination_catalog=destination_catalog,
        workspace_catalog=catalog,
        workspace_connector=RegisteredWorkspaceConnector(catalog=catalog),
        scanner=ProductionFilesystemSourceScanner(),
        budgets=SourceAdmissionBudgets(),
        signing_key=SourceAccessSigningKey("key-v1", b"k" * 32),
    )

    assert isinstance(composition.planner, BoundSourceRevisionAuthorityPlanner)
    assert isinstance(composition.job_service, KnowledgeIndexJobService)
    assert isinstance(
        composition.execution_binding_service,
        KnowledgeIndexExecutionBindingService,
    )
    assert (
        app.extensions["core_services"].knowledge.knowledge_index_job_service
        is composition.job_service
    )
    assert original is not composition.job_service

    rebuilt_queue = object()
    rebuilt = _CoreServices(
        knowledge=_KnowledgeServices(object()),
        task_queue_service=rebuilt_queue,
        ingestion_service=object(),
    )
    monkeypatch.setattr(
        "agent.services.service_registry.build_core_service_registry",
        lambda app: rebuilt,
    )

    reinitialized = initialize_core_services(app)

    assert reinitialized.task_queue_service is rebuilt_queue
    assert (
        reinitialized.knowledge.knowledge_index_job_service
        is composition.job_service
    )
