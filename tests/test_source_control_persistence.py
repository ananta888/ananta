from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from sqlmodel import SQLModel, create_engine

from agent.db_models.source_control import (
    ActiveKnowledgeIndexDB,
    ActiveKnowledgeIndexEventDB,
    KnowledgeIndexRunSourceBindingDB,
    KnowledgeIndexSourceBindingDB,
    SourceAccessGrantAuditDB,
    SourceAccessGrantDB,
    SourceConnectionDB,
    SourceControlJobEventOutboxDB,
    SourceRevisionDB,
)
from agent.repositories.source_control_repository import (
    SQLSourceControlRepository,
)
from agent.services.source_control_persistence import (
    SourceControlPersistenceError,
    SourceControlPersistenceService,
)
from ananta_contracts.source_control import (
    ConnectionState,
    DestinationDescriptor,
    GrantState,
    SourceAccessGrant,
    SourceConnection,
    SourceRevision,
)

TABLES = [
    SourceConnectionDB.__table__,
    SourceRevisionDB.__table__,
    SourceAccessGrantDB.__table__,
    SourceAccessGrantAuditDB.__table__,
    SourceControlJobEventOutboxDB.__table__,
    KnowledgeIndexSourceBindingDB.__table__,
    KnowledgeIndexRunSourceBindingDB.__table__,
    ActiveKnowledgeIndexDB.__table__,
    ActiveKnowledgeIndexEventDB.__table__,
]


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _connection() -> SourceConnection:
    return SourceConnection.create(
        tenant_id="tenant-alpha",
        project_id="project-atlas",
        owner_id="owner-alice",
        connector_type="registered_workspace",
        connection_identity_digest="a" * 64,
        display_name="Atlas workspace",
        sensitivity="internal",
        state="active",
        created_at=_timestamp("2026-07-30T00:00:00Z"),
    )


def _revision(
    connection: SourceConnection,
    *,
    digest: str = "b" * 64,
) -> SourceRevision:
    return SourceRevision.create(
        connection_id=connection.connection_id,
        tenant_id=connection.tenant_id,
        project_id=connection.project_id,
        owner_id=connection.owner_id,
        connector_type=connection.connector_type,
        sensitivity=connection.sensitivity,
        revision_token=f"snapshot-{digest[0]}",
        revision_digest=digest,
        content_manifest_id=f"manifest_{'d' * 64}",
        content_manifest_digest="d" * 64,
        admission_state="admitted",
        captured_at=_timestamp("2026-07-30T00:01:00Z"),
    )


def _destination(*, model_id: str = "model-code-small") -> DestinationDescriptor:
    return DestinationDescriptor.create(
        worker_id="worker-code-01",
        worker_kind="code_analysis",
        runtime_id="runtime-local",
        runtime_kind="local_container",
        provider_id="provider-local",
        model_id=model_id,
        model_class="code_embedding",
        provider_location="local_container",
        data_residency="host_local",
    )


def _grant(
    revision: SourceRevision,
    *,
    version: int,
    state: str,
    policy_version: str,
) -> SourceAccessGrant:
    return SourceAccessGrant.create(
        version=version,
        tenant_id=revision.tenant_id,
        project_id=revision.project_id,
        source_revision_id=revision.source_revision_id,
        destination_id=_destination().destination_id,
        operation="retrieve",
        transformation="redacted",
        purpose="code_navigation",
        policy_version=policy_version,
        state=state,
        issued_at=_timestamp(f"2026-07-{29 + version:02d}T00:00:00Z"),
        expires_at=_timestamp("2027-01-01T00:00:00Z"),
    )


def _engine(path):
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine, tables=TABLES)
    return engine


def _service(repository, clock=lambda: 1_000.0):
    return SourceControlPersistenceService(
        catalog=repository,
        grants=repository,
        indexes=repository,
        clock=clock,
    )


def _seed(repository):
    service = _service(repository)
    connection = _connection()
    revision = _revision(connection)
    service.register_connection(connection)
    service.append_revision(revision)
    return service, connection, revision


def _completed_index(service, revision, *, index_id, run_id, digest):
    index = service.bind_knowledge_index(
        knowledge_index_id=index_id,
        revision=revision,
        policy_snapshot_id="policy-snapshot-v7",
        policy_snapshot_digest="7" * 64,
        index_contract_version="knowledge-index-v1",
    )
    run = service.bind_index_run(index_run_id=run_id, index=index)
    return service.complete_index_run(
        run,
        index,
        artifact_manifest_digest=digest,
    )[0]


def test_scoped_connection_cas_and_revision_append_only(tmp_path) -> None:
    repository = SQLSourceControlRepository(
        _engine(tmp_path / "source-control.sqlite3"),
        clock=lambda: 2_000.0,
    )
    service, connection, revision = _seed(repository)

    assert repository.get_connection(
        tenant_id=connection.tenant_id,
        project_id=connection.project_id,
        owner_id="other-owner",
        connection_id=connection.connection_id,
    ) is None
    disabled = service.transition_connection(
        connection,
        target_state=ConnectionState.DISABLED,
        expected_lock_version=1,
    )
    assert disabled.contract.state is ConnectionState.DISABLED
    assert disabled.lock_version == 2
    with pytest.raises(
        SourceControlPersistenceError,
        match="source_control_version_conflict",
    ):
        service.transition_connection(
            connection,
            target_state=ConnectionState.TOMBSTONED,
            expected_lock_version=1,
        )

    assert service.append_revision(revision).contract == revision
    conflicting = SourceRevision.create(
        **{
            key: value
            for key, value in revision.to_wire().items()
            if key
            not in {
                "schema",
                "authority",
                "source_revision_id",
                "content_manifest_digest",
            }
        },
        content_manifest_digest="e" * 64,
    )
    with pytest.raises(
        SourceControlPersistenceError,
        match="source_control_revision_append_conflict",
    ):
        service.append_revision(conflicting)


def test_grant_preview_lifecycle_rollback_and_audit(tmp_path) -> None:
    repository = SQLSourceControlRepository(
        _engine(tmp_path / "source-control.sqlite3"),
        clock=lambda: _timestamp("2026-08-01T00:00:00Z").timestamp(),
    )
    service, _connection_record, revision = _seed(repository)
    draft = service.create_grant(
        _grant(revision, version=1, state="draft", policy_version="policy-v7"),
        owner_id=revision.owner_id,
    )

    assert service.preview_grant(draft).allowed is True
    active = service.transition_grant(
        draft,
        target_state=GrantState.ACTIVE,
        expected_lock_version=1,
        reason_code="approved",
    )
    with pytest.raises(
        SourceControlPersistenceError,
        match="source_control_version_conflict",
    ):
        service.transition_grant(
            draft,
            target_state=GrantState.REVOKED,
            expected_lock_version=1,
            reason_code="stale_writer",
        )
    superseded = service.transition_grant(
        active,
        target_state=GrantState.SUPERSEDED,
        expected_lock_version=2,
        reason_code="policy_replaced",
    )
    replacement = _grant(
        revision,
        version=2,
        state="active",
        policy_version="policy-v8",
    )
    rolled_back = service.rollback_grant(
        superseded,
        replacement,
        expected_previous_lock_version=3,
        reason_code="operator_rollback",
    )

    assert rolled_back.contract.state is GrantState.ACTIVE
    assert rolled_back.rollback_of_grant_id == superseded.contract.grant_id
    assert [event.action for event in repository.list_grant_audit(
        grant_id=draft.contract.grant_id
    )] == ["create", "preview", "active", "superseded"]
    assert [event.action for event in repository.list_grant_audit(
        grant_id=rolled_back.contract.grant_id
    )] == ["rollback"]


def test_index_activation_projection_and_rollback_are_deterministic(
    tmp_path,
) -> None:
    repository = SQLSourceControlRepository(
        _engine(tmp_path / "source-control.sqlite3"),
        clock=lambda: 3_000.0,
    )
    service, connection, revision = _seed(repository)
    first = _completed_index(
        service,
        revision,
        index_id="knowledge-index-001",
        run_id="index-run-001",
        digest="1" * 64,
    )
    active = service.activate_index(
        first,
        current_source_revision_id=revision.source_revision_id,
        current_policy_snapshot_digest="7" * 64,
        expected_generation=0,
    )
    second = _completed_index(
        service,
        revision,
        index_id="knowledge-index-002",
        run_id="index-run-002",
        digest="2" * 64,
    )
    promoted = service.activate_index(
        second,
        current_source_revision_id=revision.source_revision_id,
        current_policy_snapshot_digest="7" * 64,
        expected_generation=active.generation,
    )
    old_projection = repository.project_index_lifecycle(
        tenant_id=revision.tenant_id,
        project_id=revision.project_id,
        owner_id=revision.owner_id,
        connection_id=connection.connection_id,
        knowledge_index_id=first.knowledge_index_id,
        current_source_revision_id=revision.source_revision_id,
        current_policy_snapshot_digest="7" * 64,
    )
    changed_policy_projection = repository.project_index_lifecycle(
        tenant_id=revision.tenant_id,
        project_id=revision.project_id,
        owner_id=revision.owner_id,
        connection_id=connection.connection_id,
        knowledge_index_id=second.knowledge_index_id,
        current_source_revision_id=revision.source_revision_id,
        current_policy_snapshot_digest="8" * 64,
    )

    assert old_projection.superseded is True
    assert old_projection.rollback_candidate is True
    assert old_projection.stale is False
    assert changed_policy_projection.policy_changed is True
    assert changed_policy_projection.stale is True
    rolled_back = service.rollback_index(
        first,
        current_source_revision_id=revision.source_revision_id,
        current_policy_snapshot_digest="7" * 64,
        expected_generation=promoted.generation,
    )
    assert rolled_back.knowledge_index_id == first.knowledge_index_id
    assert rolled_back.previous_knowledge_index_id == second.knowledge_index_id
    assert rolled_back.generation == 3


def test_activation_crash_rolls_back_and_reconciliation_repairs(tmp_path) -> None:
    path = tmp_path / "source-control.sqlite3"
    engine = _engine(path)
    repository = SQLSourceControlRepository(engine, clock=lambda: 4_000.0)
    service, connection, revision = _seed(repository)
    candidate = _completed_index(
        service,
        revision,
        index_id="knowledge-index-crash",
        run_id="index-run-crash",
        digest="3" * 64,
    )

    crashing = SQLSourceControlRepository(
        engine,
        clock=lambda: 4_001.0,
        activation_fault_hook=lambda: (_ for _ in ()).throw(
            RuntimeError("simulated_crash")
        ),
    )
    with pytest.raises(RuntimeError, match="simulated_crash"):
        crashing.activate_index(
            tenant_id=revision.tenant_id,
            project_id=revision.project_id,
            owner_id=revision.owner_id,
            connection_id=connection.connection_id,
            knowledge_index_id=candidate.knowledge_index_id,
            current_source_revision_id=revision.source_revision_id,
            current_policy_snapshot_digest="7" * 64,
            expected_generation=0,
            action="activate",
        )
    assert repository.get_active_index(
        tenant_id=revision.tenant_id,
        project_id=revision.project_id,
        owner_id=revision.owner_id,
        connection_id=connection.connection_id,
    ) is None

    repaired = repository.reconcile_activation(
        tenant_id=revision.tenant_id,
        project_id=revision.project_id,
        owner_id=revision.owner_id,
        connection_id=connection.connection_id,
        current_source_revision_id=revision.source_revision_id,
        current_policy_snapshot_digest="7" * 64,
    )
    assert repaired.repaired is True
    assert repaired.active is not None
    assert repaired.active.knowledge_index_id == candidate.knowledge_index_id
    events = repository.list_activation_events(
        active_index_id=repaired.active.active_index_id
    )
    assert [(event.action, event.generation) for event in events] == [
        ("reconcile", 1)
    ]


def test_two_concurrent_promotions_cannot_win_the_same_generation(
    tmp_path,
) -> None:
    path = tmp_path / "source-control.sqlite3"
    repository = SQLSourceControlRepository(
        _engine(path),
        clock=lambda: 5_000.0,
    )
    service, connection, revision = _seed(repository)
    initial = _completed_index(
        service,
        revision,
        index_id="knowledge-index-initial",
        run_id="index-run-initial",
        digest="4" * 64,
    )
    active = service.activate_index(
        initial,
        current_source_revision_id=revision.source_revision_id,
        current_policy_snapshot_digest="7" * 64,
        expected_generation=0,
    )
    candidates = [
        _completed_index(
            service,
            revision,
            index_id=f"knowledge-index-racer-{number}",
            run_id=f"index-run-racer-{number}",
            digest=str(4 + number) * 64,
        )
        for number in (1, 2)
    ]

    def promote(candidate):
        contender = SQLSourceControlRepository(
            _engine(path),
            clock=lambda: 5_001.0,
        )
        try:
            return contender.activate_index(
                tenant_id=revision.tenant_id,
                project_id=revision.project_id,
                owner_id=revision.owner_id,
                connection_id=connection.connection_id,
                knowledge_index_id=candidate.knowledge_index_id,
                current_source_revision_id=revision.source_revision_id,
                current_policy_snapshot_digest="7" * 64,
                expected_generation=active.generation,
                action="activate",
            )
        except SourceControlPersistenceError as exc:
            return exc.reason_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(promote, candidates))

    winners = [result for result in results if not isinstance(result, str)]
    conflicts = [result for result in results if isinstance(result, str)]
    assert len(winners) == 1
    assert conflicts == ["source_control_generation_conflict"]
    current = repository.get_active_index(
        tenant_id=revision.tenant_id,
        project_id=revision.project_id,
        owner_id=revision.owner_id,
        connection_id=connection.connection_id,
    )
    assert current is not None
    assert current.generation == 2
    assert current.knowledge_index_id == winners[0].knowledge_index_id
