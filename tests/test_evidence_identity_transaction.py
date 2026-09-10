"""Real registry issuance and rollback under caller-owned Hub transactions."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models import TaskDB
from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.repositories.evidence_identity import EvidenceIdentityPersistenceError, SqlEvidenceIdentityRepository
from agent.repositories.evidence_identity_transaction import TransactionEvidenceIdentityRepository
from agent.repositories.task_repository_session import task_repository_session
from agent.repositories.tasks import TaskRepository
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from tests.test_hub_evidence_registry_service import _run, _source


@pytest.fixture
def evidence_database(tmp_path, monkeypatch):
    database = create_engine(
        f"sqlite:///{tmp_path / 'transactional-evidence.sqlite3'}", connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(database)
    monkeypatch.setattr("agent.repositories.tasks._engine", lambda: database)
    yield database
    database.dispose()


def registry(session):
    return HubEvidenceRegistryService(TransactionEvidenceIdentityRepository(session), clock=lambda: 10.0)


def reserve(service):
    source = _source(service, scope="test", synthetic=True)
    return _run(service, source_id=source.source_id, scope="test", synthetic=True)


def record(service, run, *, digest="d" * 64):
    return service.record_result(
        tenant_id=run.tenant_id, project_id=run.project_id, run_id=run.run_id,
        assignment_id=run.assignment_id, dispatch_lease_id=run.dispatch_lease_id,
        terminal_state="succeeded", result_digest=digest,
    )


def test_unowned_session_cannot_silently_begin_registry_work(evidence_database):
    with Session(evidence_database) as session:
        with pytest.raises(EvidenceIdentityPersistenceError, match="evidence_caller_transaction_required"):
            reserve(registry(session))
        assert not session.in_transaction()


def test_source_run_and_task_reservation_all_rollback_together(evidence_database):
    with pytest.raises(ValueError, match="synthetic-after-flush"):
        with task_repository_session(evidence_database, write=True) as session:
            run = reserve(registry(session))
            assert run.run_id.startswith("RUN_") and run.source_ids[0].startswith("SRC_")
            session.add(TaskDB(id=run.task_id, status="created"))
            session.flush()
            raise ValueError("synthetic-after-flush")
    with Session(evidence_database) as session:
        assert session.exec(select(TaskDB)).all() == []
        assert session.exec(select(HubRunEvidenceIdentityDB)).all() == []
        assert session.exec(select(HubSourceEvidenceIdentityDB)).all() == []


@pytest.mark.parametrize("reject", [False, True])
def test_task_admission_and_registry_completion_share_one_commit(evidence_database, reject):
    with task_repository_session(evidence_database, write=True) as session:
        run = reserve(registry(session))
        session.add(TaskDB(id=run.task_id, status="in_progress"))
        session.commit()

    class Policy:
        def apply(self, *, authoritative_task, candidate_task, session):
            assert record(registry(session), run).state == "succeeded"
            if reject:
                raise ValueError("synthetic-admission-rejected")
            return candidate_task

    repository = TaskRepository(completion_policy=Policy())
    task = repository.get_by_id(run.task_id)
    task.status = "completed"
    if reject:
        with pytest.raises(ValueError, match="synthetic-admission-rejected"):
            repository.save(task)
    else:
        assert repository.save(task).status == "completed"
    with Session(evidence_database) as session:
        stored_run = session.get(HubRunEvidenceIdentityDB, (run.tenant_id, run.project_id, run.run_id))
        assert stored_run.state == ("reserved" if reject else "succeeded")
        assert stored_run.result_digest == (None if reject else "d" * 64)
        assert session.get(TaskDB, run.task_id).status == ("in_progress" if reject else "completed")


def test_transactional_registry_replay_and_test_classification_are_preserved(evidence_database):
    with task_repository_session(evidence_database, write=True) as session:
        service = registry(session)
        run = reserve(service)
        assert reserve(service) == run
        projection = service.assignment_projection(
            tenant_id=run.tenant_id, project_id=run.project_id, run_id=run.run_id,
            task_id=run.task_id, assignment_id=run.assignment_id, dispatch_lease_id=run.dispatch_lease_id,
        )
        assert projection["evidence_scope"] == "test" and projection["run_id"] == run.run_id
        first = record(service, run)
        assert record(service, run) == first
        verification = service.verify_release_binding(
            tenant_id=run.tenant_id, project_id=run.project_id, run_id=run.run_id,
            required_scope="production", task_id=run.task_id, repository_revision=run.repository_revision,
            source_ids=run.source_ids,
        )
        assert not verification.verified
        with pytest.raises(EvidenceIdentityPersistenceError, match="evidence_run_terminal_replay_conflict"):
            record(service, run, digest="e" * 64)
        session.commit()
    with Session(evidence_database) as session:
        stored = session.get(HubRunEvidenceIdentityDB, (run.tenant_id, run.project_id, run.run_id))
        assert stored.result_digest == "d" * 64


def test_standalone_registry_waits_for_existing_task_transaction(evidence_database):
    attempted, finished = threading.Event(), threading.Event()
    standalone = HubEvidenceRegistryService(SqlEvidenceIdentityRepository(evidence_database), clock=lambda: 10.0)

    def independent_source_admission():
        attempted.set()
        source = _source(standalone, scope="test", synthetic=True)
        finished.set()
        return source

    with ThreadPoolExecutor(max_workers=1) as pool:
        with task_repository_session(evidence_database, write=True) as session:
            future = pool.submit(independent_source_admission)
            assert attempted.wait(2)
            assert not finished.wait(0.2), "standalone registry entered the caller's Task transaction"
            session.rollback()
        assert future.result(timeout=3).state == "admitted"


@pytest.mark.parametrize("borrowed", [False, True])
@pytest.mark.parametrize("kind", ["source", "run"])
def test_idempotency_cannot_accept_mutated_binding_fields(evidence_database, borrowed, kind):
    standalone = HubEvidenceRegistryService(SqlEvidenceIdentityRepository(evidence_database), clock=lambda: 10.0)
    run = reserve(standalone)
    with task_repository_session(evidence_database, write=True) as session:
        if kind == "source":
            row = session.get(HubSourceEvidenceIdentityDB, (run.tenant_id, run.project_id, run.source_ids[0]))
            row.content_digest = "f" * 64
        else:
            row = session.get(HubRunEvidenceIdentityDB, (run.tenant_id, run.project_id, run.run_id))
            row.environment_digest = "f" * 64
        session.add(row)
        session.commit()

    def repeat(service):
        if kind == "source":
            return _source(service, scope="test", synthetic=True)
        return _run(service, source_id=run.source_ids[0], scope="test", synthetic=True)

    with pytest.raises(EvidenceIdentityPersistenceError, match="immutable_conflict"):
        if borrowed:
            with task_repository_session(evidence_database, write=True) as session:
                repeat(registry(session))
        else:
            repeat(standalone)
