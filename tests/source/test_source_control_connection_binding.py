from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.repositories.source_control_repository import (
    SQLSourceControlRepository,
)
from agent.services.source_control_connection_binding import (
    SourceConnectionSelectorBinding,
)
from agent.services.source_control_persistence import (
    SourceControlPersistenceError,
)
from ananta_contracts.source_control import SourceConnection


def _engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _connection() -> SourceConnection:
    return SourceConnection.create(
        tenant_id="tenant-example",
        project_id="project-example",
        owner_id="owner-example",
        connector_type="github",
        connection_identity_digest="a" * 64,
        display_name="Repository",
        sensitivity="internal",
        state="draft",
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


def test_connection_and_selector_are_atomic_idempotent_and_secret_free() -> None:
    connection = _connection()
    binding = SourceConnectionSelectorBinding(
        connection_id=connection.connection_id,
        tenant_id=connection.tenant_id,
        project_id=connection.project_id,
        owner_id=connection.owner_id,
        public_connector_type="github",
        implementation_connector_type="github_repository",
        selector_kind="remote",
        selector_id="github-installation:example",
        repository_identifier="example/repository",
    )
    repository = SQLSourceControlRepository(_engine(), clock=lambda: 10.0)

    first = repository.save_connection_with_selector(connection, binding)
    replay = repository.save_connection_with_selector(connection, binding)
    stored = repository.get_connection_selector(
        tenant_id=connection.tenant_id,
        project_id=connection.project_id,
        connection_id=connection.connection_id,
    )

    assert first == replay
    assert stored == binding
    descriptor = stored.descriptor(display_name="Repository", enabled=True)
    assert descriptor["source_type"] == "github_repository"
    assert descriptor["github_authorization_ref"] == (
        "github-installation:example"
    )
    assert descriptor["repository"] == "example/repository"
    assert not any(
        key in str(descriptor).lower()
        for key in ("remote_url", "credential_ref", "secret", "host_path")
    )


def test_selector_scope_conflict_fails_closed() -> None:
    connection = _connection()
    binding = SourceConnectionSelectorBinding(
        connection_id=connection.connection_id,
        tenant_id="tenant-other",
        project_id=connection.project_id,
        owner_id=connection.owner_id,
        public_connector_type="github",
        implementation_connector_type="github_repository",
        selector_kind="remote",
        selector_id="github-installation:example",
        repository_identifier="example/repository",
    )

    with pytest.raises(
        SourceControlPersistenceError,
        match="source_control_connection_selector_scope_mismatch",
    ):
        SQLSourceControlRepository(_engine()).save_connection_with_selector(
            connection, binding
        )


def test_workspace_descriptor_contains_only_registered_relative_selector() -> None:
    binding = SourceConnectionSelectorBinding(
        connection_id=f"conn_{'b' * 64}",
        tenant_id="tenant-example",
        project_id="project-example",
        owner_id="owner-example",
        public_connector_type="local_directory",
        implementation_connector_type="local_directory",
        selector_kind="workspace",
        selector_id="workspace-example",
        relative_path="src/agent",
    )

    descriptor = binding.descriptor(display_name="Agent", enabled=True)
    assert descriptor["workspace_id"] == "workspace-example"
    assert descriptor["relative_path"] == "src/agent"
    assert descriptor["source_type"] == "local_directory"
    assert not any(
        key in descriptor
        for key in ("root", "host_path", "local_path", "absolute_path")
    )
