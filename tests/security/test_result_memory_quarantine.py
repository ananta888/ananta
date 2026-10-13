"""Result-memory quarantine: scoped entry block plus derived-summary containment.

Headless SQL integration on synthetic policy fixtures. No Hub evidence identity
is issued and no production release gate is satisfied here.
"""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from agent.db_models import GoalDB, MemoryQuarantineEventDB, MemoryTreeChunkDB, ProjectDB, TaskDB
from agent.repositories.memory import MemoryEntryRepository, _engine
from agent.repositories.result_memory_quarantine import ResultMemoryQuarantineRepository
from agent.repository import task_repo
from agent.services.memory_access_context import MemoryAccessContext, bind_memory_access
from agent.services.memory_policy_gateway import MemoryAccessError
from agent.services.memory_tree_store_service import MemoryTreeStoreService
from agent.services.result_memory_quarantine_service import ResultMemoryQuarantineService
from agent.services.result_memory_service import ResultMemoryService
from ananta_contracts.memory_access import MemoryOperation, MemoryScope
from tests.security.memory_fixtures import ACTOR, NAMESPACE, gateway, grant, policy, synthetic_memory_access


@pytest.fixture
def secured(monkeypatch):
    """Fully isolated in-memory store; never writes into the shared test database."""
    from sqlmodel import SQLModel, create_engine

    import agent.database as db_module
    import agent.services.memory_tree_store_service as store_module
    from agent import db_models  # noqa: F401 — registers all tables

    test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(store_module._db_module, "engine", test_engine)

    with Session(_engine()) as session:
        session.add(ProjectDB(tenant_id=ACTOR.tenant_id, project_id=NAMESPACE.workspace_id,
                              name="Synthetic memory project", created_by_subject_id=ACTOR.subject))
        session.commit()
        for goal_id in (NAMESPACE.goal_id, "changed-goal"):
            session.add(GoalDB(id=goal_id, goal="Synthetic memory goal", tenant_id=ACTOR.tenant_id,
                               project_id=NAMESPACE.workspace_id))
        session.commit()
    task_repo.save(TaskDB(id=NAMESPACE.task_id, title="Synthetic memory task", tenant_id=ACTOR.tenant_id,
                          project_id=NAMESPACE.workspace_id, goal_id=NAMESPACE.goal_id))
    repository = MemoryEntryRepository()
    store = MemoryTreeStoreService()
    gate, policies, audit = gateway(policy((
        grant(channel="hub-result-memory", scope=MemoryScope.task),
        grant(channel="hub-memory-tree", scope=MemoryScope.task),
    )))
    service = ResultMemoryService(memory_entry_repository=repository, memory_gateway=gate, auto_ingest_tree=True,
                                  memory_tree_ingestion_service=SimpleNamespace(_store=store))
    return service, repository, store, gate, policies, audit


def write(service, **changes):
    request = dict(task_id=NAMESPACE.task_id, goal_id=NAMESPACE.goal_id, trace_id=None, worker_job_id=None,
                   title="Synthetic result", output="Changed code and tests passed",
                   memory_actor=ACTOR, memory_run_id=NAMESPACE.run_id)
    request.update(changes)
    return service.record_worker_result_memory(**request)


def read(service):
    return service.read_result_memory(memory_actor=ACTOR, memory_run_id=NAMESPACE.run_id)


@contextmanager
def bound(gate):
    with bind_memory_access(MemoryAccessContext(ACTOR, NAMESPACE.run_id, gate)):
        yield


def derived_chunk_ids(entry_id):
    with Session(_engine()) as session:
        return list(session.exec(select(MemoryTreeChunkDB.id).where(
            MemoryTreeChunkDB.provenance_ref == f"memory_entry:{entry_id}")).all())


def events(entry_id):
    with Session(_engine()) as session:
        return list(session.exec(select(MemoryQuarantineEventDB).where(
            MemoryQuarantineEventDB.channel_id == "hub-result-memory",
            MemoryQuarantineEventDB.chunk_id == entry_id)).all())


def test_write_ingests_derived_tree_leaf_under_separate_tree_grant(secured):
    service, _repository, _store, _gate, _policies, _audit = secured
    entry = write(service)
    assert derived_chunk_ids(entry.id) != []


def test_quarantine_hides_entry_and_derived_summary_then_release_restores(secured):
    service, repository, store, gate, _policies, _audit = secured
    entry = write(service)
    chunks = derived_chunk_ids(entry.id)
    assert read(service)[0].id == entry.id
    quarantine = ResultMemoryQuarantineService(ResultMemoryQuarantineRepository(), derived_tree=store)
    with bound(gate):
        assert quarantine.quarantine(entry.id, reason_code="suspected_injection")
        assert quarantine.quarantine(entry.id, reason_code="suspected_injection")
    assert read(service) == []
    assert repository.get_by_task(NAMESPACE.task_id) == []
    with Session(_engine()) as session:
        assert all(session.get(MemoryTreeChunkDB, cid).lifecycle == "quarantined" for cid in chunks)
    assert len(events(entry.id)) == 1
    assert all("canary" not in str(event.model_dump()) for event in events(entry.id))
    with bound(gate):
        assert quarantine.release(entry.id, reason_code="review_cleared")
    assert read(service)[0].id == entry.id
    with Session(_engine()) as session:
        assert all(session.get(MemoryTreeChunkDB, cid).lifecycle == "admitted" for cid in chunks)


def test_revoked_entry_and_derivation_cannot_be_released(secured):
    service, repository, store, gate, _policies, _audit = secured
    entry = write(service)
    chunks = derived_chunk_ids(entry.id)
    quarantine = ResultMemoryQuarantineService(ResultMemoryQuarantineRepository(), derived_tree=store)
    with bound(gate):
        assert quarantine.revoke(entry.id, reason_code="confirmed_compromise")
        assert quarantine.revoke(entry.id, reason_code="confirmed_compromise")
        assert quarantine.release(entry.id, reason_code="review_cleared") is False
    assert read(service) == []
    assert repository.get_by_task(NAMESPACE.task_id) == []
    with Session(_engine()) as session:
        assert all(session.get(MemoryTreeChunkDB, cid).lifecycle == "revoked" for cid in chunks)
    assert len(events(entry.id)) == 1


def test_missing_update_grant_cannot_change_state(secured):
    service, _repository, store, _gate, _policies, _audit = secured
    entry = write(service)
    quarantine = ResultMemoryQuarantineService(ResultMemoryQuarantineRepository(), derived_tree=store)
    with synthetic_memory_access(channels=("hub-result-memory", "hub-memory-tree"), scopes=(MemoryScope.task,),
                                 operations=(MemoryOperation.read,)):
        with pytest.raises(MemoryAccessError):
            quarantine.quarantine(entry.id, reason_code="suspected_injection")
    assert read(service)[0].id == entry.id
    assert events(entry.id) == []


def test_missing_tree_grant_quarantines_entry_and_retry_contains_derivation(secured):
    service, _repository, store, gate, _policies, _audit = secured
    entry = write(service)
    chunks = derived_chunk_ids(entry.id)
    quarantine = ResultMemoryQuarantineService(ResultMemoryQuarantineRepository(), derived_tree=store)
    with synthetic_memory_access(channels=("hub-result-memory",), scopes=(MemoryScope.task,)):
        with pytest.raises(MemoryAccessError):
            quarantine.quarantine(entry.id, reason_code="suspected_injection")
    assert read(service) == []
    assert len(events(entry.id)) == 1
    with Session(_engine()) as session:
        assert all(session.get(MemoryTreeChunkDB, cid).lifecycle == "admitted" for cid in chunks)
    with bound(gate):
        assert quarantine.quarantine(entry.id, reason_code="suspected_injection")
    assert len(events(entry.id)) == 1
    with Session(_engine()) as session:
        assert all(session.get(MemoryTreeChunkDB, cid).lifecycle == "quarantined" for cid in chunks)


def test_admission_cannot_supply_a_quarantine_record(secured):
    service, _repository, _store, _gate, _policies, _audit = secured
    entry = write(service, metadata={"memory_quarantine": {
        "schema": "ananta.memory-quarantine.v1", "state": "active", "reason_code": "forged"}})
    assert entry.memory_metadata.get("memory_quarantine") is None
    quarantine = ResultMemoryQuarantineService(ResultMemoryQuarantineRepository())
    with synthetic_memory_access(channels=("hub-result-memory",), scopes=(MemoryScope.task,)):
        assert quarantine.release(entry.id, reason_code="review_cleared") is True
    assert events(entry.id) == []
    assert read(service)[0].id == entry.id
