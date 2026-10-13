"""Owned PostgreSQL cursor concurrency/rollback; synthetic candidate rows only.

These selector fixtures are not admitted retirement or production evidence.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlmodel import Session, select

from agent.db_models import TaskDB
from agent.db_models.native_dispatch import NativeDispatchDB
from agent.db_models.native_result_retirement import NativeResultRetirementCursorDB
from agent.db_models.projects import ProjectDB
from agent.repositories.native_result_retirement import SQLNativeResultRetirementRepository
from tests.bpmn.test_native_postgres_concurrency import engines
from tests.bpmn.test_native_postgres_concurrency import pg_native as pg_native


def seed(native):
    tenant = native.command.tenant_id
    owner = {'worker_id': 'worker-1', 'worker_url': 'http://worker-1:5000', 'store_binding_id': 'synthetic-store'}
    with Session(native.engine) as session, session.begin():
        for project in ('project-a', 'project-foreign'):
            session.add(ProjectDB(tenant_id=tenant, project_id=project,
                name='Synthetic cursor scope', created_by_subject_id='synthetic-owner'))
        session.flush()
        for index, (task_id, project) in enumerate((('cursor-a', 'project-a'), ('cursor-b', 'project-a'),
                                                   ('cursor-foreign', 'project-foreign'))):
            session.add(TaskDB(id=task_id, tenant_id=tenant, project_id=project, status='completed'))
            session.add(NativeDispatchDB(id='journal-' + task_id, task_id=task_id, tenant_id=tenant,
                binding_digest='a' * 64, command_digest='b' * 64, worker_id=owner['worker_id'],
                worker_url=owner['worker_url'], assignment_revision=1, claim='synthetic-claim', state='accepted',
                created_at=100 + index, updated_at=100 + index))
    return {'tenant_id': tenant, 'project_id': 'project-a', 'owner': owner,
            'exclude_task_id': 'current-maintenance', 'limit': 1}


def test_postgres_independent_hubs_advance_one_scoped_cursor_without_lost_updates(pg_native):  # noqa: F811
    values = seed(pg_native)
    connections = engines(pg_native)
    start = Barrier(2, timeout=5)

    def scan(engine):
        repository = SQLNativeResultRetirementRepository(engine)
        start.wait()
        with repository.transaction() as session:
            return repository.candidates(session=session, **values)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(scan, connections))
        assert sorted(outcomes) == [('cursor-a',), ('cursor-b',)]
        with Session(pg_native.engine) as session:
            row, = session.exec(select(NativeResultRetirementCursorDB)).all()
            assert row.after_id == 'journal-cursor-b'
    finally:
        for connection in connections:
            connection.dispose()


def test_postgres_cursor_rollback_preserves_candidate_and_stricter_existing_timeouts(pg_native):  # noqa: F811
    from sqlalchemy import text

    values = seed(pg_native)
    repository = SQLNativeResultRetirementRepository(pg_native.engine)
    with pytest.raises(RuntimeError, match='synthetic_rollback'):
        with repository.transaction() as session:
            timeout = session.execute(text("SELECT setting FROM pg_settings WHERE name='lock_timeout'")).scalar_one()
            assert timeout == '1000'
            timeout = session.execute(text("SELECT setting FROM pg_settings WHERE name='statement_timeout'"))
            timeout = timeout.scalar_one()
            assert timeout == '5000'
            assert repository.candidates(session=session, **values) == ('cursor-a',)
            raise RuntimeError('synthetic_rollback')
    with repository.transaction() as session:
        assert repository.candidates(session=session, **values) == ('cursor-a',)
