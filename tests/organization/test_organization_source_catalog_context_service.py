from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models import (
    ActiveKnowledgeIndexDB,
    ContextBundleDB,
    KnowledgeIndexDB,
    KnowledgeIndexRunDB,
    OrganizationAdminGrantDB,
    OrganizationMembershipDB,
    RetrievalRunDB,
    SourceAdmissionReceiptDB,
    SourceConnectionDB,
    SourceRevisionDB,
    TaskDB,
)
from agent.db_models.source_control import (
    KnowledgeIndexRunSourceBindingDB,
    KnowledgeIndexSourceBindingDB,
)
from agent.repositories.organization_source_catalog_repository import (
    SourceCatalogPublishingAuthority,
)
from agent.services.knowledge_index_retrieval_service import (
    KnowledgeIndexRetrievalService,
)
from agent.services.organization_source_catalog_binding_service import (
    OrganizationSourceCatalogBindingService,
    canonical_sha256,
)
from agent.services.organization_source_catalog_context_service import (
    OrganizationSourceCatalogContextError,
    OrganizationSourceCatalogContextService,
)
from agent.services.planning_artifact_transition_service import (
    PlanningOperationContext,
)
from agent.services.source_catalog_service import SourceCatalogService


def _context(subject_id: str = "operator-1") -> PlanningOperationContext:
    return PlanningOperationContext.hub_admin(
        subject_id=subject_id,
        tenant_id="tenant-1",
        project_id="project-1",
        organization_id="org-1",
    )


def _authority(*, manifest_digest: str) -> SourceCatalogPublishingAuthority:
    return SourceCatalogPublishingAuthority(
        tenant_id="tenant-1",
        project_id="project-1",
        owner_id="operator-1",
        connection_id="connection-1",
        connector_type="registered_workspace",
        sensitivity="internal",
        source_revision_id="revision-1",
        revision_digest="1" * 64,
        source_manifest_digest="2" * 64,
        admission_receipt_id="receipt-1",
        admission_digest="3" * 64,
        knowledge_index_id="index-1",
        index_run_id="run-1",
        index_source_scope="repo_path",
        index_manifest_digest=manifest_digest,
        policy_snapshot_digest="4" * 64,
        active_generation=1,
    )


@pytest.fixture()
def catalog_environment(tmp_path):
    output = tmp_path / "index-output"
    output.mkdir()
    record = {
        "id": "record-1",
        "path": "docs/hrm.md",
        "kind": "document",
        "title": "HRM architecture",
        "content": "Grounded HRM evidence",
        "start_line": 1,
        "end_line": 4,
    }
    serialized_record = json.dumps(record, sort_keys=True)
    (output / "index.jsonl").write_text(serialized_record + "\n", encoding="utf-8")
    manifest = output / "manifest.json"
    manifest.write_text('{"schema":"test-manifest.v1"}\n', encoding="utf-8")
    manifest_digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    authority = _authority(manifest_digest=manifest_digest)
    public_manifest = {
        "schema": "ananta.codecompass.artifact-manifest.v1",
        "knowledge_index_id": "index-1",
        "run_id": "run-1",
        "source_revision_id": "revision-1",
        "status": "completed",
        "manifest_digest": "5" * 64,
    }
    reader = KnowledgeIndexRetrievalService()
    # Bind the exact reconstruction returned from the persisted JSONL record;
    # mapping order is part of the current retrieval text implementation.
    content = reader._record_text(json.loads(serialized_record))
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    provenance_digest = canonical_sha256(
        {
            "schema": "organization_source_catalog_provenance.v1",
            "source_id": "SRC_0001",
            "tenant_id": authority.tenant_id,
            "project_id": authority.project_id,
            "organization_id": "org-1",
            "scope": "organization:org-1",
            "connection_id": authority.connection_id,
            "source_revision_id": authority.source_revision_id,
            "source_version": authority.revision_digest,
            "source_manifest_digest": authority.source_manifest_digest,
            "admission_digest": authority.admission_digest,
            "knowledge_index_id": authority.knowledge_index_id,
            "index_run_id": authority.index_run_id,
            "index_manifest_digest": authority.index_manifest_digest,
            "active_generation": authority.active_generation,
            "record_file": "index.jsonl",
            "record_id": "record-1",
            "path": "docs/hrm.md",
            "line_start": 1,
            "line_end": 4,
            "content_hash": content_hash,
        }
    )
    catalog = SourceCatalogService().build_catalog(
        task_id="catalog-task-1",
        retrieval_payload={
            "selected": [
                {
                    "source_id": "SRC_0001",
                    "source_version": authority.revision_digest,
                    "tenant_id": authority.tenant_id,
                    "scope": "organization:org-1",
                    "provenance_digest": provenance_digest,
                    "engine": "knowledge_index",
                    "kind": "repo_file",
                    "path": "docs/hrm.md",
                    "record_id": "record-1",
                    "line_start": 1,
                    "line_end": 4,
                    "content_hash": content_hash,
                    "manifest_hash": manifest_digest,
                    "sensitivity": "internal",
                }
            ],
            "retrieval_trace": {
                "trace_id": "catalog-trace-1",
                "context_hash": "6" * 64,
                "manifest_hash": manifest_digest,
                "tenant_id": "tenant-1",
                "scope": "organization:org-1",
            },
        },
    )
    publication = OrganizationSourceCatalogBindingService().build(
        organization_id="org-1",
        authority=authority,
        query_digests=["7" * 64],
        query_limit=20,
        record_bindings=[
            {
                "source_id": "SRC_0001",
                "record_file": "index.jsonl",
                "record_id": "record-1",
                "path": "docs/hrm.md",
                "line_start": 1,
                "line_end": 4,
                "content_hash": content_hash,
            }
        ],
    )
    projection = {
        "schema": catalog["schema"],
        "source_catalog_id": catalog["catalog_id"],
        "source_catalog_hash": catalog["catalog_hash"],
        "catalog_state": catalog["catalog_state"],
        "source_count": 1,
        "rejected_count": 0,
        "retrieval_trace_id": catalog["retrieval_trace_id"],
        "retrieval_context_hash": catalog["retrieval_context_hash"],
        "retrieval_manifest_hash": catalog["retrieval_manifest_hash"],
        "sources": catalog["sources"],
    }

    database = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        database,
        tables=[
            SourceConnectionDB.__table__,
            SourceRevisionDB.__table__,
            SourceAdmissionReceiptDB.__table__,
            KnowledgeIndexDB.__table__,
            KnowledgeIndexRunDB.__table__,
            KnowledgeIndexSourceBindingDB.__table__,
            KnowledgeIndexRunSourceBindingDB.__table__,
            ActiveKnowledgeIndexDB.__table__,
            OrganizationMembershipDB.__table__,
            OrganizationAdminGrantDB.__table__,
            TaskDB.__table__,
            RetrievalRunDB.__table__,
            ContextBundleDB.__table__,
        ],
    )
    with Session(database) as session:
        session.add(
            SourceConnectionDB(
                connection_id="connection-1",
                tenant_id="tenant-1",
                project_id="project-1",
                owner_id="operator-1",
                connector_type="registered_workspace",
                connection_identity_digest="8" * 64,
                display_name="Research bundle",
                sensitivity="internal",
                state="active",
                lock_version=1,
                created_at_epoch=1,
                updated_at_epoch=1,
            )
        )
        session.add(
            SourceRevisionDB(
                source_revision_id="revision-1",
                connection_id="connection-1",
                tenant_id="tenant-1",
                project_id="project-1",
                owner_id="operator-1",
                connector_type="registered_workspace",
                sensitivity="internal",
                revision_token="exact-revision",
                revision_digest="1" * 64,
                content_manifest_id="manifest-1",
                content_manifest_digest="2" * 64,
                admission_state="admitted",
                captured_at_epoch=1,
            )
        )
        session.add(
            SourceAdmissionReceiptDB(
                receipt_id="receipt-1",
                tenant_id="tenant-1",
                project_id="project-1",
                source_revision_id="revision-1",
                decision_state="admitted",
                reason_codes=[],
                revision_digest="1" * 64,
                manifest_digest="2" * 64,
                policy_digest="9" * 64,
                inventory_evidence_digest="a" * 64,
                scan_evidence_digest="b" * 64,
                admission_digest="3" * 64,
                file_count=1,
                total_bytes=100,
                largest_file_bytes=100,
                archive_expansion_ratio=1,
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
                evaluated_at_epoch=1,
                persisted_at_epoch=1,
            )
        )
        session.add(
            KnowledgeIndexDB(
                id="index-1",
                latest_run_id="run-1",
                source_scope="repo_path",
                status="completed",
                output_dir=str(output),
                manifest_path=str(manifest),
                index_metadata={"artifact_manifest": public_manifest},
            )
        )
        session.add(
            KnowledgeIndexRunDB(
                id="run-1",
                knowledge_index_id="index-1",
                status="completed",
                output_dir=str(output),
                manifest_path=str(manifest),
                run_metadata={"artifact_manifest": public_manifest},
            )
        )
        session.add(
            KnowledgeIndexSourceBindingDB(
                knowledge_index_id="index-1",
                tenant_id="tenant-1",
                project_id="project-1",
                owner_id="operator-1",
                connection_id="connection-1",
                source_revision_id="revision-1",
                policy_snapshot_id="policy-1",
                policy_snapshot_digest="4" * 64,
                index_contract_version="v1",
                status="completed",
                artifact_manifest_digest=manifest_digest,
                activation_requested=True,
                lock_version=1,
                created_at_epoch=1,
                updated_at_epoch=1,
            )
        )
        session.add(
            KnowledgeIndexRunSourceBindingDB(
                index_run_id="run-1",
                knowledge_index_id="index-1",
                tenant_id="tenant-1",
                project_id="project-1",
                owner_id="operator-1",
                source_revision_id="revision-1",
                policy_snapshot_id="policy-1",
                policy_snapshot_digest="4" * 64,
                status="completed",
                artifact_manifest_digest=manifest_digest,
                artifacts_verified=True,
                lock_version=1,
                created_at_epoch=1,
                completed_at_epoch=1,
            )
        )
        session.add(
            ActiveKnowledgeIndexDB(
                active_index_id="active-1",
                tenant_id="tenant-1",
                project_id="project-1",
                owner_id="operator-1",
                connection_id="connection-1",
                source_revision_id="revision-1",
                policy_snapshot_digest="4" * 64,
                knowledge_index_id="index-1",
                generation=1,
                updated_at_epoch=1,
            )
        )
        session.add(
            TaskDB(
                id="catalog-task-1",
                status="completed",
                tenant_id="tenant-1",
                project_id="project-1",
                organization_id="org-1",
                task_kind="source_catalog",
                history=[
                    {
                        "event_type": "task_ingested",
                        "actor": "operator-1",
                        "details": {"source": "api"},
                    }
                ],
                verification_status={
                    "source_catalog": projection,
                    "source_catalog_publication": publication,
                },
            )
        )
        for subject_id in ("operator-1", "operator-2"):
            session.add(
                OrganizationMembershipDB(
                    tenant_id="tenant-1",
                    project_id="project-1",
                    organization_id="org-1",
                    principal_id=subject_id,
                    membership_kind="organization_admin",
                )
            )
            session.add(
                OrganizationAdminGrantDB(
                    grant_id=f"catalog-research-grant-{subject_id}",
                    tenant_id="tenant-1",
                    project_id="project-1",
                    organization_id="org-1",
                    principal_id=subject_id,
                    grant_kind="planning:category_research",
                    policy_hash="6" * 64,
                    granted_by="operator-1",
                )
            )
        session.commit()
    binding = {
        "catalog_task_id": "catalog-task-1",
        "catalog_id": catalog["catalog_id"],
        "catalog_hash": catalog["catalog_hash"],
        "repository_revision": authority.revision_digest,
        "manifest_hash": authority.index_manifest_digest,
        "source_allowlist_version": catalog["catalog_hash"],
        "source_scope": "organization:org-1",
    }
    return database, output, binding


def test_materialize_maps_hub_source_id_to_hash_verified_task_bundle(
    catalog_environment,
) -> None:
    database, _output, binding = catalog_environment
    with Session(database) as session:
        result = OrganizationSourceCatalogContextService().materialize(
            session,
            context=_context(),
            catalog_binding=binding,
            task_id="research-task-1",
            goal_id="goal-1",
        )
        assert result.context_bundle.task_id == "research-task-1"
        assert result.context_bundle.chunks[0]["metadata"]["source_id"] == "SRC_0001"
        assert result.context_bundle.chunks[0]["metadata"]["source_id_verified"] is True
        assert result.context_bundle.chunks[0]["content"]
        session.commit()

    with Session(database) as session:
        bundle = session.exec(select(ContextBundleDB)).one()
        run = session.exec(select(RetrievalRunDB)).one()
        catalog_task = session.get(TaskDB, "catalog-task-1")
    assert "Grounded HRM evidence" in str(bundle.context_text)
    assert "Grounded HRM evidence" not in repr(catalog_task.verification_status)
    assert "Grounded HRM evidence" not in repr(run.run_metadata)


def test_materialize_authorizes_current_organization_admin_not_catalog_publisher(
    catalog_environment,
) -> None:
    database, _output, binding = catalog_environment

    with Session(database) as session:
        result = OrganizationSourceCatalogContextService().materialize(
            session,
            context=_context("operator-2"),
            catalog_binding=binding,
            task_id="research-task-by-second-admin",
            goal_id="goal-1",
        )

    assert result.resolved_catalog.catalog_task_id == "catalog-task-1"
    assert result.context_bundle.task_id == "research-task-by-second-admin"


def test_materialize_rejects_revoked_current_organization_grant(
    catalog_environment,
) -> None:
    database, _output, binding = catalog_environment
    with Session(database) as session:
        grant = session.get(
            OrganizationAdminGrantDB,
            "catalog-research-grant-operator-2",
        )
        assert grant is not None
        grant.revoked_at = 1.0
        session.add(grant)
        session.commit()

    with Session(database) as session, pytest.raises(
        OrganizationSourceCatalogContextError,
        match="category_research_source_catalog_authority_forbidden",
    ):
        OrganizationSourceCatalogContextService().materialize(
            session,
            context=_context("operator-2"),
            catalog_binding=binding,
            task_id="research-task-after-revocation",
            goal_id="goal-1",
        )


def test_materialize_rejects_tampered_record_and_stale_active_index(
    catalog_environment,
) -> None:
    database, output, binding = catalog_environment
    tampered = {
        "id": "record-1",
        "path": "docs/hrm.md",
        "kind": "document",
        "title": "HRM architecture",
        "content": "tampered evidence",
        "start_line": 1,
        "end_line": 4,
    }
    (output / "index.jsonl").write_text(json.dumps(tampered) + "\n", encoding="utf-8")
    with Session(database) as session, pytest.raises(
        OrganizationSourceCatalogContextError,
        match="knowledge_index_bound_record_content_mismatch",
    ):
        OrganizationSourceCatalogContextService().materialize(
            session,
            context=_context(),
            catalog_binding=binding,
            task_id="research-task-1",
            goal_id="goal-1",
        )

    database, _output, binding = catalog_environment
    with Session(database) as session:
        active = session.get(ActiveKnowledgeIndexDB, "active-1")
        active.knowledge_index_id = "index-new"
        session.add(active)
        session.commit()
    with Session(database) as session, pytest.raises(
        OrganizationSourceCatalogContextError,
        match="organization_source_catalog_active_index_changed",
    ):
        OrganizationSourceCatalogContextService().materialize(
            session,
            context=_context(),
            catalog_binding=binding,
            task_id="research-task-1",
            goal_id="goal-1",
        )
