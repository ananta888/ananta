from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from agent.db_models import AgentInfoDB, KnowledgeIndexDB, KnowledgeIndexRunDB
from agent.db_models.context_policy_lifecycle import ContextPolicyVersionDB
from agent.db_models.source_control import (
    KnowledgeIndexRunSourceBindingDB,
    KnowledgeIndexSourceBindingDB,
    SourceAccessGrantDB,
    SourceConnectionDB,
    SourceRevisionDB,
)
from agent.services.codecompass_graph_window_service import (
    CodeCompassGraphWindowService,
)
from agent.services.source_control_production_adapters import (
    ContainedArtifactDeletionService,
    HubBoundSourceIndexSubmissionAdapter,
    HubSourceControlOperationsAdapter,
    ScopedWorkerModelDestinationCatalog,
    SourceControlProductionAdapterError,
    build_scoped_effective_access_service,
    derive_policy_snapshot_id,
)
from agent.services.source_control_projection_service import (
    SourceControlPrincipal,
)
from ananta_contracts.model_catalog import (
    ModelAvailability,
    ModelHealth,
)
from ananta_contracts.source_control import (
    GrantOperation,
    GrantTransformation,
)


def _engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@dataclass(frozen=True)
class _Model:
    provider_id: str = "ollama"
    model_id: str = "code-model"
    availability: ModelAvailability = ModelAvailability.AVAILABLE
    health: ModelHealth = ModelHealth.HEALTHY
    capabilities: tuple[str, ...] = ("class:code",)


def test_active_graph_projects_codecompass_edge_identifiers() -> None:
    class _Store:
        def load(self):
            return {
                "state": {"manifest_hash": "sha256:graph"},
                "nodes": [{"id": "n1"}, {"id": "n2"}],
                "edges": [
                    {
                        "source_id": "n1",
                        "target_id": "n2",
                        "edge_type": "declares",
                    }
                ],
                "semantic_nodes": [{"id": "semantic:n1"}],
                "semantic_edges": [
                    {
                        "source_id": "n1",
                        "target_id": "semantic:n1",
                        "edge_type": "semantic_declares",
                    }
                ],
            }

        def load_visual_metrics(self):
            return None

    class _Projection:
        def __init__(self) -> None:
            self.nodes = []
            self.edges = []

        def project(self, **kwargs):
            self.nodes = list(kwargs["nodes"])
            self.edges = list(kwargs["edges"])
            return {"metadata": dict(kwargs["metadata"])}

    projection = _Projection()
    adapter = object.__new__(HubSourceControlOperationsAdapter)
    adapter._projection = projection
    adapter._active_index = lambda **_kwargs: type("Index", (), {"id": "index-1"})()
    adapter._graph_store = lambda _index: _Store()
    adapter._artifact_projection = lambda _index: {"status": "verified"}

    result = adapter.graph(parameters={"limit": 100})

    assert projection.nodes == [{"id": "n1"}, {"id": "n2"}]
    assert projection.edges == [
        {
            "source_id": "n1",
            "target_id": "n2",
            "edge_type": "declares",
        }
    ]
    assert result["text_alternative"] == "Graph with 2 nodes and 1 edges."


def test_topology_graph_view_uses_injected_connected_window() -> None:
    class _Store:
        def load(self):
            return {
                "state": {"manifest_hash": "sha256:graph"},
                "nodes": [
                    {"id": "isolated"},
                    {"id": "leaf-b"},
                    {"id": "hub"},
                    {"id": "leaf-a"},
                ],
                "edges": [
                    {
                        "edge_id": "hub-a",
                        "source_id": "hub",
                        "target_id": "leaf-a",
                        "edge_type": "declares",
                    },
                    {
                        "edge_id": "hub-b",
                        "source_id": "hub",
                        "target_id": "leaf-b",
                        "edge_type": "declares",
                    },
                    {
                        "edge_id": "leaf-semantic",
                        "source_id": "leaf-a",
                        "target_id": "semantic:service",
                        "edge_type": "declares",
                    },
                    {
                        "edge_id": "dangling",
                        "source_id": "hub",
                        "target_id": "semantic:missing",
                        "edge_type": "declares",
                    },
                ],
                "semantic_nodes": [{"id": "semantic:service"}],
                "semantic_edges": [
                    {
                        "edge_id": "semantic-hub",
                        "source_id": "semantic:service",
                        "target_id": "hub",
                        "edge_type": "implements",
                    }
                ],
                "diagnostics": {
                    "semantic_translation": {
                        "status": "degraded",
                        "reason": "semantic_graph_partial",
                        "semantic_budget": {
                            "configured_max_records_per_partition": 5000,
                            "max_records_per_partition": 5000,
                            "max_bytes_per_partition": 4194304,
                            "configuration_clamped": False,
                            "truncated": True,
                            "truncated_node_count": 3,
                            "truncated_edge_count": 1,
                            "unresolved_edge_count": 2,
                            "semantic_node_bytes": 100,
                            "semantic_edge_bytes": 50,
                        },
                    }
                },
            }

        def load_visual_metrics(self):
            return None

    class _Projection:
        def __init__(self) -> None:
            self.kwargs = {}

        def project(self, **kwargs):
            self.kwargs = kwargs
            return {"metadata": dict(kwargs["metadata"])}

    projection = _Projection()
    adapter = object.__new__(HubSourceControlOperationsAdapter)
    adapter._projection = projection
    adapter._graph_window = CodeCompassGraphWindowService()
    adapter._active_index = (
        lambda **_kwargs: type("Index", (), {"id": "index-1"})()
    )
    adapter._graph_store = lambda _index: _Store()
    adapter._artifact_projection = lambda _index: {"status": "verified"}

    result = adapter.graph(
        parameters={"limit": 4, "view": "topology", "max_edges": 4}
    )

    assert [node["id"] for node in projection.kwargs["nodes"]] == [
        "hub",
        "leaf-a",
        "semantic:service",
        "leaf-b",
    ]
    assert [edge["edge_id"] for edge in projection.kwargs["edges"]] == [
        "hub-a",
        "hub-b",
        "leaf-semantic",
        "semantic-hub",
    ]
    assert projection.kwargs["derive_projection_revision"] is True
    assert result["metadata"] == {
        "knowledge_index_id": "index-1",
        "view": "topology",
        "next_cursor": None,
        "total_nodes": 5,
        "total_edges": 4,
        "source_edge_count": 5,
        "unresolved_edge_count": 1,
        "internal_edge_count": 4,
        "edge_capped": False,
        "max_edges": 4,
        "semantic_budget": {
            "configured_max_records_per_partition": 5000,
            "max_records_per_partition": 5000,
            "max_bytes_per_partition": 4194304,
            "configuration_clamped": False,
            "truncated": True,
            "truncated_node_count": 3,
            "truncated_edge_count": 1,
            "unresolved_edge_count": 2,
            "semantic_node_bytes": 100,
            "semantic_edge_bytes": 50,
        },
    }
    assert result["text_alternative"] == (
        "Topology graph window with 4 nodes and 4 edges out of 5 nodes."
    )
    assert projection.kwargs["warnings"] == [
        "1 graph relation was excluded because a source or target node is "
        "unavailable. Reindex the source to materialize current semantic nodes.",
        "The semantic graph reached its configured record budget; the topology "
        "is a documented partial view.",
        "2 semantic graph relations were not materialized because no "
        "source-grounded endpoint was available.",
    ]
    assert projection.kwargs["diagnostics"]["semantic_translation"]["status"] == (
        "degraded"
    )


def test_destination_catalog_requires_server_scope_and_model_evidence() -> None:
    engine = _engine()
    with Session(engine) as db:
        db.add(
            AgentInfoDB(
                url="http://worker.test",
                name="worker-example",
                role="worker",
                status="online",
                registration_validated=True,
                runtime_targets=[
                    {
                        "runtime_id": "runtime-example",
                        "runtime_kind": "ollama",
                        "provider_id": "ollama",
                        "model_id": "code-model",
                        "model_class": "code",
                        "provider_location": "private_network",
                        "data_residency": "eu",
                        "source_access_authorized": True,
                        "tenant_id": "tenant-example",
                        "project_id": "project-example",
                    }
                ],
            )
        )
        db.commit()
    catalog = ScopedWorkerModelDestinationCatalog(
        engine=engine,
        model_supplier=lambda: (_Model(),),
    )

    allowed, _ = catalog.list(
        tenant_id="tenant-example",
        project_id="project-example",
        cursor=None,
        limit=10,
        filters={},
    )
    denied, _ = catalog.list(
        tenant_id="other-tenant",
        project_id="project-example",
        cursor=None,
        limit=10,
        filters={},
    )

    assert len(allowed) == 1
    assert allowed[0].worker_id == "worker-example"
    assert denied == ()
    assert (
        catalog.get(
            tenant_id="tenant-example",
            project_id="project-example",
            destination_id=allowed[0].destination_id,
        )
        == allowed[0]
    )


def test_effective_access_uses_scoped_destination_and_persistent_grant() -> None:
    engine = _engine()
    policy_digest = "9" * 64
    policy_snapshot_id = derive_policy_snapshot_id(
        tenant_id="tenant-example",
        project_id="project-example",
        policy_id="policy-example",
        version=1,
        policy_digest=policy_digest,
    )
    with Session(engine) as db:
        db.add(
            AgentInfoDB(
                url="http://worker.test",
                name="worker-example",
                role="worker",
                status="online",
                registration_validated=True,
                runtime_targets=[
                    {
                        "runtime_id": "runtime-example",
                        "runtime_kind": "ollama",
                        "provider_id": "ollama",
                        "model_id": "code-model",
                        "model_class": "code",
                        "provider_location": "private_network",
                        "data_residency": "eu",
                        "source_access_authorized": True,
                        "tenant_id": "tenant-example",
                        "project_id": "project-example",
                    }
                ],
            )
        )
        db.add(
            SourceConnectionDB(
                connection_id=f"conn_{'a' * 64}",
                tenant_id="tenant-example",
                project_id="project-example",
                owner_id="owner-example",
                connector_type="github",
                connection_identity_digest="c" * 64,
                display_name="Example",
                sensitivity="internal",
                state="active",
                lock_version=1,
                created_at_epoch=1.0,
                updated_at_epoch=1.0,
            )
        )
        db.add(
            SourceRevisionDB(
                source_revision_id=f"srev_{'b' * 64}",
                connection_id=f"conn_{'a' * 64}",
                tenant_id="tenant-example",
                project_id="project-example",
                owner_id="owner-example",
                connector_type="github",
                sensitivity="internal",
                revision_token="main",
                revision_digest="d" * 64,
                content_manifest_id=f"manifest_{'e' * 64}",
                content_manifest_digest="f" * 64,
                admission_state="admitted",
                captured_at_epoch=1.0,
            )
        )
        db.add(
            ContextPolicyVersionDB(
                record_id=policy_snapshot_id,
                tenant_id="tenant-example",
                project_id="project-example",
                policy_id="policy-example",
                version=1,
                state="active",
                document_json={"policy_id": "policy-example", "version": 1},
                policy_digest=policy_digest,
                etag=policy_digest,
                created_by="owner-example",
                created_at="2026-01-01T00:00:00Z",
                updated_by="owner-example",
                updated_at="2026-01-01T00:00:00Z",
            )
        )
        db.commit()
    destinations = ScopedWorkerModelDestinationCatalog(
        engine=engine,
        model_supplier=lambda: (_Model(),),
    )
    available, _ = destinations.list(
        tenant_id="tenant-example",
        project_id="project-example",
        cursor=None,
        limit=10,
        filters={},
    )
    with Session(engine) as db:
        db.add(
            SourceAccessGrantDB(
                grant_id=f"grant_{'1' * 64}",
                grant_family_id="grant-family-example",
                grant_version=1,
                tenant_id="tenant-example",
                project_id="project-example",
                owner_id="owner-example",
                source_revision_id=f"srev_{'b' * 64}",
                destination_id=available[0].destination_id,
                operation=GrantOperation.INDEX.value,
                transformation=GrantTransformation.REDACTED.value,
                purpose="code-review",
                policy_version=policy_snapshot_id,
                policy_snapshot_digest=policy_digest,
                state="active",
                issued_at_epoch=1.0,
                expires_at_epoch=4_102_444_800.0,
                lock_version=1,
                updated_at_epoch=1.0,
            )
        )
        db.commit()
    service = build_scoped_effective_access_service(
        engine=engine,
        destinations=destinations,
        tenant_id="tenant-example",
        project_id="project-example",
    )

    decision = service.preview(
        tenant_id="tenant-example",
        project_id="project-example",
        source_revision_id=f"srev_{'b' * 64}",
        destination_id=available[0].destination_id,
        operation=GrantOperation.INDEX,
        transformation=GrantTransformation.REDACTED,
        purpose="code-review",
    )

    assert decision.decision == "allow"
    assert decision.reason_codes == ("active_grant",)


def test_bound_index_submission_keeps_scope_and_access_intent_hub_owned() -> None:
    class Planner:
        def plan_bound_source_revision(self, **kwargs):
            assert set(kwargs) == {
                "tenant_id",
                "project_id",
                "actor_id",
                "connection_id",
                "source_revision_id",
                "source_revision_digest",
                "content_manifest_digest",
                "descriptor",
                "idempotency_key",
            }
            return {
                "hub_task_id": "hub-task-example",
                "source_revision_id": kwargs["source_revision_id"],
                "source_revision_digest": kwargs[
                    "source_revision_digest"
                ],
                "admission_digest": "1" * 64,
                "policy_snapshot_id": "policy-example-v1",
                "policy_snapshot_digest": "2" * 64,
                "destination_id": f"dest_{'3' * 64}",
                "destination_digest": "4" * 64,
                "source_access_grant_id": f"grant_{'5' * 64}",
                "source_access_grant_digest": "6" * 64,
                "files": [],
                "resource_budget": {},
                "assignment": {},
                "destination_selection": {},
                "source_scope": "github",
                "source_id": "source-example",
                "source_payload_digest": "7" * 64,
                "source_payload_connection_id": kwargs["connection_id"],
                "records": [],
            }

    class Jobs:
        def __init__(self) -> None:
            self.arguments = None

        def submit_bound_source_revision_job(self, **kwargs):
            self.arguments = kwargs
            return {"job_id": "job-example", "status": "todo"}

    jobs = Jobs()
    adapter = HubBoundSourceIndexSubmissionAdapter(
        planner=Planner(),
        job_service=jobs,
    )
    connection = SourceConnectionDB(
        connection_id=f"conn_{'a' * 64}",
        tenant_id="tenant-example",
        project_id="project-example",
        owner_id="owner-example",
        connector_type="github",
        connection_identity_digest="c" * 64,
        display_name="Example",
        sensitivity="internal",
        state="active",
        lock_version=1,
        created_at_epoch=1.0,
        updated_at_epoch=1.0,
    )
    revision = SourceRevisionDB(
        source_revision_id=f"srev_{'b' * 64}",
        connection_id=connection.connection_id,
        tenant_id=connection.tenant_id,
        project_id=connection.project_id,
        owner_id=connection.owner_id,
        connector_type=connection.connector_type,
        sensitivity=connection.sensitivity,
        revision_token="main",
        revision_digest="d" * 64,
        content_manifest_id=f"manifest_{'e' * 64}",
        content_manifest_digest="f" * 64,
        admission_state="admitted",
        captured_at_epoch=1.0,
    )

    result = adapter.submit(
        connection=connection,
        revision=revision,
        descriptor={"source_id": "source-example"},
        actor_id="admin-example",
        idempotency_key="idempotency-example",
        profile_name="code-profile",
    )

    assert result == {"job_id": "job-example", "status": "todo"}
    assert jobs.arguments["tenant_id"] == "tenant-example"
    assert jobs.arguments["project_id"] == "project-example"
    assert "source_payload_digest" not in jobs.arguments
    assert "source_payload_connection_id" not in jobs.arguments
    assert jobs.arguments["owner_id"] == "owner-example"
    assert jobs.arguments["created_by"] == "admin-example"
    assert jobs.arguments["source_operation"] == "index"
    assert jobs.arguments["source_transformation"] == "redacted"
    assert jobs.arguments["source_purpose"] == "knowledge-index"
    assert jobs.arguments["source_scope"] == "repo_path"


def _seed_deletable_index(engine, output_dir, manifest_digest) -> None:
    connection_id = f"conn_{'a' * 64}"
    revision_id = f"srev_{'b' * 64}"
    with Session(engine) as db:
        db.add(
            SourceConnectionDB(
                connection_id=connection_id,
                tenant_id="tenant-example",
                project_id="project-example",
                owner_id="owner-example",
                connector_type="github",
                connection_identity_digest="c" * 64,
                display_name="Example",
                sensitivity="internal",
                state="disabled",
                lock_version=1,
                created_at_epoch=1.0,
                updated_at_epoch=1.0,
            )
        )
        db.add(
            SourceRevisionDB(
                source_revision_id=revision_id,
                connection_id=connection_id,
                tenant_id="tenant-example",
                project_id="project-example",
                owner_id="owner-example",
                connector_type="github",
                sensitivity="internal",
                revision_token="main",
                revision_digest="d" * 64,
                content_manifest_id=f"manifest_{'e' * 64}",
                content_manifest_digest="f" * 64,
                admission_state="admitted",
                captured_at_epoch=1.0,
            )
        )
        db.add(
            KnowledgeIndexSourceBindingDB(
                knowledge_index_id="index-example",
                tenant_id="tenant-example",
                project_id="project-example",
                owner_id="owner-example",
                connection_id=connection_id,
                source_revision_id=revision_id,
                policy_snapshot_id="policy-example",
                policy_snapshot_digest="1" * 64,
                index_contract_version="v1",
                status="tombstoned",
                artifact_manifest_digest=manifest_digest,
                lock_version=2,
                created_at_epoch=1.0,
                updated_at_epoch=1.0,
            )
        )
        db.add(
            KnowledgeIndexRunSourceBindingDB(
                index_run_id="run-example",
                knowledge_index_id="index-example",
                tenant_id="tenant-example",
                project_id="project-example",
                owner_id="owner-example",
                source_revision_id=revision_id,
                policy_snapshot_id="policy-example",
                policy_snapshot_digest="1" * 64,
                status="completed",
                artifact_manifest_digest=manifest_digest,
                artifacts_verified=True,
                lock_version=1,
                created_at_epoch=1.0,
                completed_at_epoch=2.0,
            )
        )
        db.add(
            KnowledgeIndexDB(
                id="index-example",
                latest_run_id="run-example",
                source_scope="github",
                status="completed",
                output_dir=str(output_dir),
                manifest_path=str(output_dir / "manifest.json"),
                index_metadata={
                    "retention_released": True,
                    "retention_approval_id": "approval-example",
                },
                created_by="owner-example",
            )
        )
        db.add(
            KnowledgeIndexRunDB(
                id="run-example",
                knowledge_index_id="index-example",
                status="completed",
                output_dir=str(output_dir),
                manifest_path=str(output_dir / "manifest.json"),
            )
        )
        db.commit()


def test_artifact_deletion_is_contained_audited_and_replay_safe(
    tmp_path,
) -> None:
    engine = _engine()
    root = tmp_path / "knowledge_indices"
    output = root / "github" / "index-example" / "run-example"
    output.mkdir(parents=True)
    manifest = output / "manifest.json"
    manifest.write_text('{"schema":"test-manifest.v1"}', encoding="utf-8")
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    _seed_deletable_index(engine, output, digest)
    service = ContainedArtifactDeletionService(
        engine=engine,
        artifact_root=root,
    )
    principal = SourceControlPrincipal(
        subject_id="admin-example",
        tenant_id="tenant-example",
        project_id="project-example",
        roles=frozenset({"admin"}),
    )

    first = service.delete(
        principal=principal,
        knowledge_index_id="index-example",
        expected_version=2,
        approval_id="approval-example",
    )
    replay = service.delete(
        principal=principal,
        knowledge_index_id="index-example",
        expected_version=2,
        approval_id="approval-example",
    )

    assert first == replay
    assert not output.exists()
    assert service.is_deleted(knowledge_index_id="index-example")


def test_artifact_deletion_rejects_path_outside_allowed_root(
    tmp_path,
) -> None:
    engine = _engine()
    root = tmp_path / "knowledge_indices"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    manifest = outside / "manifest.json"
    manifest.write_text('{"schema":"test-manifest.v1"}', encoding="utf-8")
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    _seed_deletable_index(engine, outside, digest)
    service = ContainedArtifactDeletionService(
        engine=engine,
        artifact_root=root,
    )

    with pytest.raises(
        SourceControlProductionAdapterError,
        match="artifact_output_outside_root",
    ):
        service.delete(
            principal=SourceControlPrincipal(
                subject_id="admin-example",
                tenant_id="tenant-example",
                project_id="project-example",
                roles=frozenset({"admin"}),
            ),
            knowledge_index_id="index-example",
            expected_version=2,
            approval_id="approval-example",
        )
