"""Real PostgreSQL lock/transaction checks; only an owned fixture DB is allowed."""

import copy
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from threading import Barrier

import pytest
from sqlalchemy import create_engine, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from agent.common.task_mutation_lock import TaskMutationLockPort
from agent.db_models import TaskDB
from agent.db_models.native_dispatch import NativeDispatchDB
from agent.db_models.workflow_runtime import WorkflowExecutionOwnershipDB
from agent.repositories.tasks import TaskRepository
from agent.services.native_dispatch_journal import SQLAlchemyNativeDispatchJournal
from tests.bpmn import test_native_result_admission as native_module
from tests.bpmn.test_native_dispatch_journal import worker
from tests.pi import test_pi_result_completion_policy as admission_module


@pytest.fixture
def pg_native(tmp_path, monkeypatch):
    raw = os.environ.get('ANANTA_TEST_NATIVE_POSTGRES_URL')
    if not raw:
        pytest.skip('Explicit owned PostgreSQL fixture required')
    url = make_url(raw)
    if (url.drivername != 'postgresql+psycopg2' or url.host != '127.0.0.1'
            or url.database != 'ananta_bpmn_fixture' or url.username != 'ananta_fixture'
            or url.password != 'synthetic-postgres-fixture-password' or not url.port):
        raise ValueError('native_postgres_fixture_target_denied')
    schema = 'bpmn_test_' + uuid.uuid4().hex
    admin = create_engine(url, connect_args={'connect_timeout': 5})
    with admin.begin() as connection:
        connection.execute(text('CREATE SCHEMA ' + schema))
    scoped = url.update_query_dict({'options': '-csearch_path=' + schema
        + ' -cstatement_timeout=5000 -clock_timeout=1000'})
    engine = create_engine(scoped, connect_args={'connect_timeout': 5})
    lifecycle = None
    try:
        monkeypatch.setattr(admission_module, 'create_engine', lambda ignored: engine)
        lifecycle = admission_module.admission.__wrapped__(tmp_path, monkeypatch)
        native = native_module.native.__wrapped__(next(lifecycle))
        yield native
    finally:
        if lifecycle is not None:
            lifecycle.close()
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text('DROP SCHEMA ' + schema + ' CASCADE'))
        admin.dispose()


def engines(native):
    return [create_engine(native.engine.url, connect_args={'connect_timeout': 5}) for _ in range(2)]


def test_postgres_parallel_hubs_get_one_durable_dispatch_claim(pg_native):
    task = pg_native.repository.get_by_id(pg_native.task_id)
    connections = engines(pg_native)
    start = Barrier(2, timeout=5)

    def reserve(engine):
        start.wait()
        try:
            SQLAlchemyNativeDispatchJournal(engine).begin(task=task, assignment=worker(task))
            return 'send'
        except ValueError as exc:
            return str(exc)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(reserve, connections))
        assert sorted(outcomes) == ['native_dispatch_already_reserved', 'send']
        with Session(pg_native.engine) as session:
            rows = session.exec(select(NativeDispatchDB)).all()
            assert len(rows) == 1 and rows[0].state == 'sending'
    finally:
        for engine in connections:
            engine.dispose()


@pytest.mark.parametrize('different_output', [False, True], ids=['exact_replay', 'contradictory_result'])
def test_postgres_parallel_terminal_admission_keeps_one_exact_receipt(pg_native, monkeypatch, different_output):
    connections = engines(pg_native)
    current = ContextVar('postgres_test_engine', default=pg_native.engine)
    locks = ContextVar('postgres_test_hub_locks', default=TaskMutationLockPort(engine_provider=current.get))
    monkeypatch.setattr('agent.repositories.tasks._engine', current.get)
    monkeypatch.setattr('agent.common.task_mutation_lock.get_task_mutation_lock_port', locks.get)
    candidates = [pg_native.repository.get_by_id(pg_native.task_id) for _ in connections]
    for index, candidate in enumerate(candidates):
        response = copy.deepcopy(pg_native.response)
        if different_output and index:
            nested = response['workflow_adapter_verification']['workflow_adapter_task_result']['adapter_result']
            nested['output_data']['output'] = 'contradictory worker result'
            nested['verification']['native_node_result']['output_data']['output'] = 'contradictory worker result'
        fields = admission_module.result_fields(response)
        candidate.status = 'completed'
        for name, value in fields.items():
            setattr(candidate, name, value)
    start = Barrier(2, timeout=5)

    def admit(pair):
        engine, candidate = pair
        token = current.set(engine)
        lock_token = locks.set(TaskMutationLockPort(engine_provider=lambda: engine))
        try:
            start.wait()
            repository = TaskRepository(completion_policy=pg_native.policy)
            try:
                receipt = repository.save(candidate).verification_status['native_result_receipt']
                return 'accepted', receipt
            except ValueError as exc:
                return 'denied', str(exc)
        finally:
            locks.reset(lock_token)
            current.reset(token)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(admit, zip(connections, candidates, strict=True)))
        receipts = [receipt for status, receipt in outcomes if status == 'accepted']
        assert receipts
        if different_output:
            denied = [receipt for status, receipt in outcomes if status == 'denied']
            assert denied == ['native_result_receipt_immutable']
            assert len(receipts) == 1
        else:
            assert len(receipts) == 2 and receipts[0] == receipts[1]
        stored = pg_native.repository.get_by_id(pg_native.task_id)
        assert stored.status == 'completed'
        assert stored.verification_status['native_result_receipt'] == receipts[0]
    finally:
        for engine in connections:
            engine.dispose()


def test_postgres_result_authority_lock_blocks_concurrent_lease_mutation(pg_native):
    connections = engines(pg_native)
    try:
        with Session(connections[0]) as session, session.begin():
            task = session.get(TaskDB, pg_native.task_id)
            pg_native.policy._authority.require_current(session=session, task=task,
                command=pg_native.command, now=pg_native.clock.now)
            ownership = session.exec(select(WorkflowExecutionOwnershipDB)).one()
            ownership_id = ownership.id
            superseding_ownership = {**ownership.ownership, 'fencing_token': 99}
            with pytest.raises(OperationalError) as failure:
                with connections[1].begin() as concurrent:
                    concurrent.execute(update(WorkflowExecutionOwnershipDB).where(
                        WorkflowExecutionOwnershipDB.id == ownership_id).values(fencing_token=99))
            assert failure.value.orig.pgcode == '55P03'  # PostgreSQL lock_timeout, not a generic failure.
        with connections[1].begin() as concurrent:
            changed = concurrent.execute(update(WorkflowExecutionOwnershipDB).where(
                WorkflowExecutionOwnershipDB.id == ownership_id).values(
                    fencing_token=99, ownership=superseding_ownership))
            assert changed.rowcount == 1
        with pytest.raises(ValueError, match='lease_not_current'):
            admission_module.complete(pg_native)
        assert pg_native.repository.get_by_id(pg_native.task_id).status == 'in_progress'
    finally:
        for engine in connections:
            engine.dispose()


def test_postgres_unknown_settlement_cannot_downgrade_concurrent_acceptance(pg_native):
    task = pg_native.repository.get_by_id(pg_native.task_id)
    claim = SQLAlchemyNativeDispatchJournal(pg_native.engine).begin(task=task, assignment=worker(task))
    accepted = admission_module.complete(pg_native)
    connections = engines(pg_native)
    start = Barrier(2, timeout=5)

    def settle(pair):
        engine, success = pair
        start.wait()
        SQLAlchemyNativeDispatchJournal(engine).settle(task=accepted, claim=claim, accepted=success)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(settle, zip(connections, (True, False), strict=True)))
        with Session(pg_native.engine) as session:
            assert session.exec(select(NativeDispatchDB)).one().state == 'accepted'
    finally:
        for engine in connections:
            engine.dispose()


def test_postgres_caller_rollback_removes_dispatch_and_task_transition(pg_native):
    journal = SQLAlchemyNativeDispatchJournal(pg_native.engine)
    with pytest.raises(RuntimeError, match='synthetic_commit_failure'):
        with Session(pg_native.engine) as session, session.begin():
            task = session.get(TaskDB, pg_native.task_id)
            journal.begin_in_transaction(session=session, task=task, assignment=worker(task))
            task.status = 'running'
            session.flush()
            raise RuntimeError('synthetic_commit_failure')
    with Session(pg_native.engine) as session:
        assert session.exec(select(NativeDispatchDB)).all() == []
        assert session.get(TaskDB, pg_native.task_id).status == 'in_progress'
