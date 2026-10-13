"""Headless SQL integration using synthetic policy fixtures, not release evidence."""

from dataclasses import asdict, replace

import pytest

from agent.db_models import MemoryEntryDB, TaskDB
from agent.repositories.memory import MemoryEntryRepository
from agent.repository import task_repo
from agent.services.memory_policy_gateway import MemoryAccessError
from agent.services.result_memory_service import ResultMemoryService
from ananta_contracts.memory_access import MemoryScope, MemorySecurityMetadata, MemorySensitivity
from tests.security.memory_fixtures import ACTOR, NAMESPACE, gateway, grant, policy


@pytest.fixture
def secured(app):
    from sqlmodel import Session

    from agent.db_models import GoalDB, ProjectDB
    from agent.repositories.memory import _engine

    with Session(_engine()) as session:
        session.add(ProjectDB(tenant_id=ACTOR.tenant_id, project_id=NAMESPACE.workspace_id,
                              name="Synthetic memory project", created_by_subject_id=ACTOR.subject))
        session.commit()
        for goal_id in (NAMESPACE.goal_id, "changed-goal"):
            session.add(GoalDB(id=goal_id, goal="Synthetic memory goal", tenant_id=ACTOR.tenant_id,
                               project_id=NAMESPACE.workspace_id))
        session.commit()
    task_repo.save(TaskDB(id=NAMESPACE.task_id, title="Synthetic memory task", tenant_id=ACTOR.tenant_id,
                         project_id=NAMESPACE.workspace_id,
                         goal_id=NAMESPACE.goal_id))
    repository = MemoryEntryRepository()
    gate, policies, audit = gateway(policy((grant(channel="hub-result-memory", scope=MemoryScope.task),)))
    service = ResultMemoryService(memory_entry_repository=repository, memory_gateway=gate)
    return service, repository, policies, audit


def write(service, **changes):
    request = dict(task_id=NAMESPACE.task_id, goal_id=NAMESPACE.goal_id, trace_id=None, worker_job_id=None,
                   title="Synthetic result", output="Changed code and tests passed",
                   memory_actor=ACTOR, memory_run_id=NAMESPACE.run_id)
    request.update(changes)
    return service.record_worker_result_memory(**request)


def test_legacy_internal_label_uses_current_hub_grants(secured):
    service, _repository, policies, audit = secured
    entry = write(service, policy={"sensitivity": "project_internal"})
    assert entry is not None
    assert entry.memory_metadata["memory_security"]["sensitivity"] == "internal"
    assert audit.decisions[-1].allowed is True
    policies.value = None
    with pytest.raises(MemoryAccessError):
        write(service, policy={"sensitivity": "project_internal"}, approved=True)


def read(service):
    return service.read_result_memory(memory_actor=ACTOR, memory_run_id=NAMESPACE.run_id)


def seed(repository, namespace, *, scope=MemoryScope.task, sensitivity=MemorySensitivity.internal, **changes):
    metadata = {
        "memory_namespace": asdict(namespace), "memory_security_schema": "ananta.memory-security.v1",
        "memory_security": MemorySecurityMetadata(scope=scope, sensitivity=sensitivity).to_mapping(),
    }
    metadata.update(changes)
    return repository.save(MemoryEntryDB(task_id=namespace.task_id, goal_id=namespace.goal_id,
                                         content="synthetic canary", memory_metadata=metadata))


def test_guarded_write_persists_authoritative_namespace_and_read_returns_only_that_task(secured):
    service, repository, _policies, _audit = secured
    entry = write(service, approved=True, metadata={"memory_namespace": {"tenant_id": "forged"},
                                                  "memory_security": {"scope": "global"}})
    assert entry.memory_metadata["memory_namespace"] == asdict(NAMESPACE)
    assert entry.memory_metadata["memory_security"]["trust_level"] == "untrusted"
    assert entry.memory_metadata["memory_security"]["scope"] == "task"
    assert len(entry.memory_metadata["memory_policy_digest"]) == 64
    assert [item.id for item in read(service)] == [entry.id]
    legacy = repository.save(MemoryEntryDB(task_id=NAMESPACE.task_id, content="legacy unscoped canary"))
    assert legacy.id not in [item.id for item in read(service)]


@pytest.mark.parametrize("field", ["tenant_id", "workspace_id", "goal_id", "task_id"])
def test_foreign_namespace_is_filtered_in_sql(secured, field):
    service, repository, _policies, _audit = secured
    current = write(service)
    namespace = replace(NAMESPACE, **{field: "foreign"})
    if field == "task_id":
        task_repo.save(TaskDB(id=namespace.task_id, title="Other task", tenant_id=ACTOR.tenant_id))
    seed(repository, namespace)
    assert [item.id for item in read(service)] == [current.id]


def test_metadata_cannot_claim_tenant_of_another_authoritative_task(secured):
    _service, repository, _policies, _audit = secured
    task_repo.save(TaskDB(id="foreign-task", title="Foreign tenant task", tenant_id="foreign-tenant"))
    forged = replace(NAMESPACE, task_id="foreign-task")
    seed(repository, forged)
    assert repository.get_by_namespace(forged, MemoryScope.task) == []


def test_goal_changed_on_actual_task_invalidates_old_namespace(secured):
    service, _repository, _policies, _audit = secured
    write(service)
    task = task_repo.get_by_id(NAMESPACE.task_id)
    task.goal_id = "changed-goal"
    task_repo.save(task)
    assert read(service) == []


def test_scope_and_sensitivity_are_filtered_before_returning_content(secured):
    service, repository, _policies, _audit = secured
    current = write(service)
    seed(repository, NAMESPACE, scope=MemoryScope.global_)
    seed(repository, NAMESPACE, sensitivity=MemorySensitivity.secret)
    assert [item.id for item in read(service)] == [current.id]


@pytest.mark.parametrize("expiry", [0, "bad-expiry", float("inf")])
def test_expired_or_invalid_expiry_is_not_retrieved(secured, expiry):
    service, repository, _policies, _audit = secured
    seed(repository, NAMESPACE, expires_at=expiry)
    assert read(service) == []


@pytest.mark.parametrize("field", ["task_id", "goal_id"])
def test_request_ids_cannot_override_authoritative_run_namespace(secured, field):
    service, repository, _policies, _audit = secured
    with pytest.raises(MemoryAccessError, match="result_binding_mismatch"):
        write(service, **{field: "foreign"})
    assert repository.get_by_task(NAMESPACE.task_id) == []


def test_policy_revoked_during_compaction_prevents_sql_write(secured, monkeypatch):
    service, repository, policies, _audit = secured
    original = service._compact_output

    def revoked(output):
        policies.value = replace(policies.value, capabilities=())
        return original(output)

    monkeypatch.setattr(service, "_compact_output", revoked)
    with pytest.raises(MemoryAccessError, match="capability_missing"):
        write(service)
    assert repository.get_by_task(NAMESPACE.task_id) == []


def test_policy_revoked_during_sql_read_prevents_content_return(secured, monkeypatch):
    service, repository, policies, _audit = secured
    write(service)
    original = repository.get_by_namespace

    def revoked(*args, **kwargs):
        rows = original(*args, **kwargs)
        assert rows
        policies.value = replace(policies.value, capabilities=())
        return rows

    monkeypatch.setattr(repository, "get_by_namespace", revoked)
    with pytest.raises(MemoryAccessError, match="capability_missing"):
        read(service)


def test_missing_run_context_is_denied_before_persistence(secured):
    service, repository, _policies, _audit = secured
    with pytest.raises(MemoryAccessError, match="run_context_required"):
        write(service, memory_actor=None, memory_run_id=None)
    assert repository.get_by_task(NAMESPACE.task_id) == []


def test_auto_tree_ingest_needs_separate_tree_grant_and_cannot_use_approval_flag(secured):
    from types import SimpleNamespace

    from sqlmodel import Session, select

    from agent.db_models import MemoryTreeChunkDB
    from agent.repositories.memory import _engine
    from agent.services.memory_tree_store_service import MemoryTreeStoreService

    _service, repository, _policies, _audit = secured
    gate, *_ = gateway(policy((grant(channel="hub-result-memory", scope=MemoryScope.task),)))
    service = ResultMemoryService(memory_entry_repository=repository, memory_gateway=gate, auto_ingest_tree=True,
                                  memory_tree_ingestion_service=SimpleNamespace(_store=MemoryTreeStoreService()))
    assert write(service, approved=True) is not None
    with Session(_engine()) as session:
        assert session.exec(select(MemoryTreeChunkDB)).all() == []


def test_legacy_write_cannot_spoof_new_security_namespace(secured):
    _service, repository, _policies, _audit = secured
    legacy = ResultMemoryService(memory_entry_repository=repository)
    entry = write(legacy, memory_actor=None, memory_run_id=None, metadata={
        "memory_namespace": asdict(NAMESPACE), "memory_security_schema": "ananta.memory-security.v1",
        "memory_security": {"scope": "global", "trust_level": "source_verified"},
        "memory_policy_digest": "a" * 64,
    })
    assert entry is None
    assert repository.get_by_namespace(NAMESPACE, MemoryScope.task) == []


def test_migration_is_bounded_conservative_and_idempotent(secured):
    _service, repository, _policies, _audit = secured
    for identifier in ("migration-first", "migration-second"):
        repository.save(MemoryEntryDB(id=identifier, task_id=NAMESPACE.task_id, memory_metadata={
            "memory_scope": "global", "approved": True, "source_refs": ["synthetic-tool"],
            "memory_namespace": asdict(NAMESPACE),
        }))
    first = repository.migrate_security_metadata(limit=1)
    assert first == {"scanned": 1, "updated": 1, "next_cursor": "migration-first"}
    second = repository.migrate_security_metadata(limit=1, after_id=first["next_cursor"])
    assert second["updated"] == 1 and second["next_cursor"] == "migration-second"
    assert repository.migrate_security_metadata(limit=1, after_id=second["next_cursor"])["scanned"] == 0
    assert repository.migrate_security_metadata()["updated"] == 0
    for identifier in ("migration-first", "migration-second"):
        entry = repository.get_by_id(identifier)
        assert entry.memory_metadata["memory_security"] == MemorySecurityMetadata().to_mapping()
        assert "memory_namespace" not in entry.memory_metadata
        assert entry.memory_metadata["source_refs"] == ["synthetic-tool"]


def test_invalid_new_security_metadata_rolls_back_migration_batch(secured):
    _service, repository, _policies, _audit = secured
    repository.save(MemoryEntryDB(id="migration-first", memory_metadata={"approved": True}))
    repository.save(MemoryEntryDB(id="migration-second", memory_metadata={
        "memory_security_schema": "ananta.memory-security.v1", "memory_security": {"scope": "invalid"},
    }))
    with pytest.raises(ValueError):
        repository.migrate_security_metadata()
    assert repository.get_by_id("migration-first").memory_metadata == {"approved": True}


def test_migration_dry_run_reports_changes_and_unclassified_rows_without_writing(secured):
    _service, repository, _policies, _audit = secured
    original = {"approved": True, "memory_scope": "global", "source_refs": ["synthetic-tool"]}
    repository.save(MemoryEntryDB(id="migration-first", memory_metadata=original))
    repository.save(MemoryEntryDB(id="migration-second", memory_metadata={
        "memory_security_schema": "ananta.memory-security.v1", "memory_security": {"scope": "invalid"},
    }))
    preview = repository.migrate_security_metadata(dry_run=True)
    assert preview["updated"] == 0 and preview["would_update"] == 1
    assert preview["unclassified"] == [{"entry_id": "migration-second",
                                        "reason_code": "memory_security_metadata_invalid"}]
    assert preview["dry_run"] and not preview["production_release_evidence"]
    assert repository.get_by_id("migration-first").memory_metadata == original
    assert repository.migrate_security_metadata(dry_run=True) == preview


def test_journaled_migration_can_be_restored_and_rollback_is_idempotent(secured):
    _service, repository, _policies, _audit = secured
    original = {"approved": True, "memory_scope": "global", "nested": {"canary": "synthetic"}}
    repository.save(MemoryEntryDB(id="rollback-first", memory_metadata=original))
    repository.migrate_security_metadata(batch_id="synthetic-migration")
    assert repository.get_by_id("rollback-first").memory_metadata != original
    assert repository.rollback_security_metadata("synthetic-migration")["restored"] == 1
    assert repository.get_by_id("rollback-first").memory_metadata == original
    assert repository.rollback_security_metadata("synthetic-migration")["restored"] == 0


@pytest.mark.parametrize("change", ["metadata", "task", "goal", "deleted"])
def test_rollback_conflict_restores_no_other_rows(secured, change):
    from sqlmodel import Session

    from agent.repositories.memory import _engine

    _service, repository, _policies, _audit = secured
    for identifier in ("rollback-first", "rollback-second"):
        repository.save(MemoryEntryDB(id=identifier, memory_metadata={"approved": True}))
    repository.migrate_security_metadata(batch_id="synthetic-conflict")
    first_snapshot = repository.get_by_id("rollback-first").memory_metadata
    row = repository.get_by_id("rollback-second")
    if change == "metadata":
        row.memory_metadata = {**row.memory_metadata, "later_update": True}
    elif change == "task":
        row.task_id = "later-task"
    elif change == "goal":
        row.goal_id = "later-goal"
    if change == "deleted":
        with Session(_engine()) as session:
            session.delete(session.get(MemoryEntryDB, row.id))
            session.commit()
    else:
        repository.save(row)
    with pytest.raises(ValueError, match="memory_migration_rollback_conflict"):
        repository.rollback_security_metadata("synthetic-conflict")
    assert repository.get_by_id("rollback-first").memory_metadata == first_snapshot


def test_migration_batch_reuse_unknown_rollback_and_dry_run(secured):
    _service, repository, _policies, _audit = secured
    repository.save(MemoryEntryDB(id="rollback-first", memory_metadata={"approved": True}))
    repository.migrate_security_metadata(batch_id="synthetic-dry-run", dry_run=True)
    with pytest.raises(ValueError, match="memory_migration_batch_unknown"):
        repository.rollback_security_metadata("synthetic-dry-run")
    repository.migrate_security_metadata(batch_id="synthetic-unique")
    with pytest.raises(ValueError, match="memory_migration_batch_exists"):
        repository.migrate_security_metadata(batch_id="synthetic-unique")


def test_namespace_admission_uses_actual_sql_owner_and_never_legacy_labels(secured):
    _service, repository, _policies, _audit = secured
    original = {"memory_scope": "global", "approved": True, "memory_namespace": {"tenant_id": "forged"}}
    entry = repository.save(MemoryEntryDB(id="admit-legacy", task_id=NAMESPACE.task_id,
                                         goal_id=NAMESPACE.goal_id, memory_metadata=original))
    assert repository.get_by_namespace(NAMESPACE, MemoryScope.task) == []
    preview = repository.migrate_security_metadata(dry_run=True, admit_namespaces=True)
    assert preview["would_update"] == 1 and preview["unclassified"] == []
    assert repository.get_by_id(entry.id).memory_metadata == original
    repository.migrate_security_metadata(batch_id="synthetic-owner-admission", admit_namespaces=True)
    migrated = repository.get_by_id(entry.id)
    assert migrated.memory_metadata["memory_namespace"]["tenant_id"] == ACTOR.tenant_id
    assert migrated.memory_metadata["memory_namespace"]["workspace_id"] == NAMESPACE.workspace_id
    assert migrated.memory_metadata["memory_security"]["scope"] == "task"
    assert migrated.memory_metadata["memory_security"]["trust_level"] == "untrusted"
    assert [row.id for row in repository.get_by_namespace(NAMESPACE, MemoryScope.task)] == [entry.id]
    assert repository.migrate_security_metadata(admit_namespaces=True)["updated"] == 0
    repository.rollback_security_metadata("synthetic-owner-admission")
    assert repository.get_by_id(entry.id).memory_metadata == original


def test_unowned_or_mismatched_legacy_memory_is_quarantined_by_admission(secured):
    _service, repository, _policies, _audit = secured
    repository.save(MemoryEntryDB(id="orphan-memory", memory_metadata={"approved": True}))
    repository.save(MemoryEntryDB(id="mismatched-goal-memory", task_id=NAMESPACE.task_id,
                                 goal_id="foreign-goal", memory_metadata={"approved": True}))
    preview = repository.migrate_security_metadata(dry_run=True, admit_namespaces=True)
    assert len(preview["unclassified"]) == 2
    assert all(row["reason_code"] == "memory_owner_namespace_unclassified" for row in preview["unclassified"])
    repository.migrate_security_metadata(batch_id="synthetic-quarantine", admit_namespaces=True)
    assert repository.get_by_namespace(NAMESPACE, MemoryScope.task) == []
    for identifier in ("orphan-memory", "mismatched-goal-memory"):
        metadata = repository.get_by_id(identifier).memory_metadata
        assert "memory_namespace" not in metadata
        assert metadata["memory_admission"]["classification"] == "quarantined"
