"""Bounded SQLite authority races at the actual Task repository policy seam."""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from sqlmodel import Session, SQLModel, create_engine

from agent.db_models import TaskDB
from agent.repositories.tasks import TaskRepository
from agent.services.workflow_runtime.sqlalchemy_support import SQLAlchemyStoreSupport


@pytest.fixture
def repository_engine(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'task-admission.sqlite3'}",
        connect_args={"check_same_thread": False, "timeout": 0.05},
    )
    SQLModel.metadata.create_all(engine)
    metadata = sa.MetaData()
    authority = sa.Table("test_admission_authority", metadata, sa.Column("revision", sa.Integer, nullable=False))
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(authority.insert().values(revision=1))
    with Session(engine) as session:
        session.add(TaskDB(id="test-task", status="in_progress"))
        session.commit()
    monkeypatch.setattr("agent.repositories.tasks._engine", lambda: engine)
    yield engine, authority
    engine.dispose()


def terminalize(repository, mode):
    if mode == "save":
        task = repository.get_by_id("test-task")
        task.status = "completed"
        return repository.save(task)
    result = repository.compare_and_set_status(
        "test-task", expected_statuses={"in_progress"}, target_status="completed",
    )
    assert result.updated
    return result.task


@pytest.mark.parametrize("mode", ["save", "cas"])
def test_task_policy_holds_sqlite_write_snapshot_before_validating_authority(repository_engine, mode):
    engine, authority = repository_engine
    observations = []

    class Policy:
        def apply(self, *, authoritative_task, candidate_task, session):
            assert authoritative_task.status == "in_progress" and candidate_task.status == "completed"
            before = session.execute(sa.select(authority.c.revision)).scalar_one()
            try:
                with engine.begin() as competing:
                    competing.execute(authority.update().values(revision=2))
            except sa.exc.OperationalError as exc:
                assert "locked" in str(exc).lower()
                observations.append("write-excluded")
            else:
                observations.append("authority-mutated-before-task-commit")
            assert before == session.execute(sa.select(authority.c.revision)).scalar_one() == 1
            return candidate_task

    assert terminalize(TaskRepository(completion_policy=Policy()), mode).status == "completed"
    assert observations == ["write-excluded"]


@pytest.mark.parametrize("mode", ["save", "cas"])
def test_task_policy_and_workflow_store_share_existing_sqlite_serialization(repository_engine, mode):
    engine, authority = repository_engine
    entered, attempted, finished, release = (threading.Event() for _ in range(4))

    class Policy:
        def apply(self, *, authoritative_task, candidate_task, session):
            entered.set()
            assert release.wait(3), "bounded test coordinator did not release policy"
            return candidate_task

    class WorkflowStore(SQLAlchemyStoreSupport):
        def change_authority(self):
            attempted.set()
            with self._transaction() as session:
                session.execute(authority.update().values(revision=2))
            finished.set()

    repository, workflow = TaskRepository(completion_policy=Policy()), WorkflowStore(engine)
    with ThreadPoolExecutor(max_workers=2) as pool:
        write = pool.submit(terminalize, repository, mode)
        assert entered.wait(2), "repository never reached policy"
        competing = pool.submit(workflow.change_authority)
        try:
            assert attempted.wait(2)
            assert not finished.wait(0.2), "workflow authority changed inside the Task policy transaction"
        finally:
            release.set()
        assert write.result(timeout=3).status == "completed"
        competing.result(timeout=3)
    assert finished.is_set()
    with engine.connect() as connection:
        assert connection.execute(sa.select(authority.c.revision)).scalar_one() == 2


def test_policy_failure_rolls_back_task_and_releases_database_writer(repository_engine):
    engine, authority = repository_engine

    class Policy:
        def apply(self, **kwargs):
            raise ValueError("synthetic_admission_denied")

    repository = TaskRepository(completion_policy=Policy())
    with pytest.raises(ValueError, match="synthetic_admission_denied"):
        terminalize(repository, "cas")
    assert repository.get_by_id("test-task").status == "in_progress"
    with engine.begin() as connection:
        connection.execute(authority.update().values(revision=3))


def test_task_reader_waits_for_workflow_transaction_before_opening_a_session(repository_engine):
    engine, authority = repository_engine
    entered, attempted, finished, release = (threading.Event() for _ in range(4))

    class WorkflowStore(SQLAlchemyStoreSupport):
        def update(self):
            with self._transaction() as session:
                session.execute(authority.update().values(revision=4))
                entered.set()
                assert release.wait(3)

    def read():
        attempted.set()
        task = TaskRepository().get_by_id("test-task")
        finished.set()
        return task

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(WorkflowStore(engine).update)
        assert entered.wait(2)
        reader = pool.submit(read)
        try:
            assert attempted.wait(2)
            assert not finished.wait(0.2), "Task read entered another workflow transaction"
        finally:
            release.set()
        writer.result(timeout=3)
        assert reader.result(timeout=3).id == "test-task"
    with engine.connect() as connection:
        assert connection.execute(sa.select(authority.c.revision)).scalar_one() == 4


def test_non_sqlite_session_does_not_replace_backend_transaction_policy(monkeypatch):
    from agent.repositories.task_repository_session import task_repository_session

    engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    session = Mock()
    factory = Mock(return_value=nullcontext(session))
    monkeypatch.setattr("agent.repositories.task_repository_session.Session", factory)
    with task_repository_session(engine, write=True) as actual:
        assert actual is session
    factory.assert_called_once_with(engine)
    session.execute.assert_not_called()
