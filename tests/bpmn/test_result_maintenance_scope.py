"""Actual SQL control project scope through the productive Hub queue composition."""

from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from agent.db_models import TaskDB
from agent.db_models.projects import ProjectDB
from agent.repositories.tasks import TaskRepository
from agent.services.native_graph_orchestration_service import NativeGraphRequest
from agent.services.native_graph_task_queue_adapter import build_native_graph_task_queue_adapter
from tests.bpmn.test_native_task_execution_http import signed as signed  # noqa: F401
from tests.bpmn.test_result_maintenance_node import cleanup as cleanup  # noqa: F401
from tests.bpmn.test_result_maintenance_node import maintenance_plan
from tests.test_native_graph_task_adapters import FakeQueue, FakeRepository


@pytest.fixture
def submission(cleanup, tmp_path, monkeypatch):  # noqa: F811 - imported pytest fixture
    request = NativeGraphRequest(maintenance_plan(), 'synthetic-maintenance-run', 'synthetic-control',
        input_data={'batch_size': 100})
    cleanup.hub.start(request)
    command = cleanup.queue.submissions[0]
    database = create_engine(f"sqlite:///{tmp_path / 'maintenance-scope.sqlite3'}")
    SQLModel.metadata.create_all(database)
    monkeypatch.setattr('agent.services.native_result_maintenance_scope._engine', lambda: database)
    monkeypatch.setattr('agent.repositories.tasks._engine', lambda: database)
    with Session(database) as session, session.begin():
        session.add(ProjectDB(tenant_id=command.tenant_id, project_id='project-1', name='Synthetic maintenance project',
            created_by_subject_id='synthetic-test-subject'))
        session.add(TaskDB(id=command.control_task_id, tenant_id=command.tenant_id,
            project_id='project-1', status='running'))
    repository = FakeRepository()
    queue = FakeQueue(repository)
    monkeypatch.setattr('agent.repository.task_repo', repository)
    monkeypatch.setattr('agent.services.task_queue_service.get_task_queue_service', lambda: queue)
    monkeypatch.setattr('agent.services.native_result_maintenance_scope.get_repository_registry',
        lambda: SimpleNamespace(task_repo=TaskRepository()))
    yield SimpleNamespace(database=database, command=command, queue=queue, repository=repository,
        adapter=build_native_graph_task_queue_adapter())
    database.dispose()


def test_real_control_project_is_inherited_before_maintenance_task_ingestion(submission):
    receipt = submission.adapter.submit(submission.command)
    task = submission.repository.get_by_id(receipt.hub_task_id)
    assert task.tenant_id == submission.command.tenant_id and task.project_id == 'project-1'
    assert task.parent_task_id == submission.command.control_task_id
    assert len(submission.queue.ingested_values) == 1
    assert submission.adapter.submit(submission.command).accepted
    assert len(submission.queue.ingested_values) == 1


@pytest.mark.parametrize('change', ['missing_parent', 'foreign_tenant', 'no_project', 'terminal_parent',
                                  'archived_project', 'missing_project'])
def test_invalid_control_scope_never_ingests_maintenance_work(submission, change):
    with Session(submission.database) as session, session.begin():
        task = session.get(TaskDB, submission.command.control_task_id)
        project = session.get(ProjectDB, (submission.command.tenant_id, 'project-1'))
        if change == 'missing_parent':
            session.delete(task)
        elif change == 'foreign_tenant':
            task.tenant_id = 'foreign'
        elif change == 'no_project':
            task.project_id = None
        elif change == 'terminal_parent':
            task.status = 'completed'
        elif change == 'archived_project':
            project.status = 'archived'
        else:
            session.delete(project)
    with pytest.raises(ValueError):
        submission.adapter.submit(submission.command)
    assert submission.queue.ingested_values == []


def test_duplicate_command_cannot_accept_another_persisted_project_scope(submission):
    receipt = submission.adapter.submit(submission.command)
    task = submission.repository.get_by_id(receipt.hub_task_id)
    task.project_id = 'foreign-project'
    with pytest.raises(ValueError, match='scope_changed_before_ingestion'):
        submission.adapter.submit(submission.command)
    assert len(submission.queue.ingested_values) == 1
