from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.repositories.source_control_repository import (
    SQLSourceControlRepository,
)
from agent.services.source_control_connection_binding import (
    SourceConnectionSelectorBinding,
)
from agent.services.source_control_production_adapters import (
    HubSourceControlOperationsAdapter,
)
from ananta_contracts.source_control import SourceConnection


class _EmptyRegistry:
    def list_sources(self, *, include_disabled):
        assert include_disabled is True
        return ()


class _Refresh:
    def __init__(self) -> None:
        self.descriptor = None

    def refresh_descriptor(self, *, descriptor, dry_run):
        assert dry_run is False
        self.descriptor = dict(descriptor)
        return {"source_id": descriptor["source_id"], "status": "ok"}


def test_operations_resolve_persistent_selector_before_legacy_registry() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    connection = SourceConnection.create(
        tenant_id="tenant-example",
        project_id="project-example",
        owner_id="owner-example",
        connector_type="local_directory",
        connection_identity_digest="a" * 64,
        display_name="Sources",
        sensitivity="internal",
        state="draft",
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    binding = SourceConnectionSelectorBinding(
        connection_id=connection.connection_id,
        tenant_id=connection.tenant_id,
        project_id=connection.project_id,
        owner_id=connection.owner_id,
        public_connector_type="local_directory",
        implementation_connector_type="local_directory",
        selector_kind="workspace",
        selector_id="workspace-example",
        relative_path="src",
    )
    SQLSourceControlRepository(engine).save_connection_with_selector(
        connection, binding
    )
    refresh = _Refresh()
    operations = HubSourceControlOperationsAdapter(
        engine=engine,
        registry=_EmptyRegistry(),
        refresh=refresh,
        index_submission=None,
        graph_resolver=object(),
        graph_projection=object(),
    )

    result = operations.refresh(
        tenant_id=connection.tenant_id,
        project_id=connection.project_id,
        actor_id=connection.owner_id,
        connection_id=connection.connection_id,
        payload={},
        idempotency_key="example-key",
    )

    assert result["status"] == "ok"
    assert refresh.descriptor["source_type"] == "local_directory"
    assert refresh.descriptor["workspace_id"] == "workspace-example"
    assert refresh.descriptor["relative_path"] == "src"
    assert "root" not in refresh.descriptor
