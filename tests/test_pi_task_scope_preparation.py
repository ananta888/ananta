"""Production Pi scope derives only from real persisted Hub project facts."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from agent.db_models import TaskDB
from agent.db_models.projects import ProjectDB
from agent.repositories.tasks import TaskRepository
from agent.services.native_graph_task_queue_adapter import build_native_graph_task_queue_adapter
from tests.test_native_graph_task_adapters import FakeQueue, FakeRepository
from tests.test_pi_hub_budget_composition import composition
from tests.test_pi_native_node import task_command


@pytest.fixture
def scoped_submission(tmp_path, monkeypatch):
    _, context, _ = composition()
    command = task_command(context)
    database = create_engine(f"sqlite:///{tmp_path / 'pi-project-scope.sqlite3'}")
    SQLModel.metadata.create_all(database)
    monkeypatch.setattr("agent.services.pi_task_scope_composition._engine", lambda: database)
    monkeypatch.setattr("agent.repositories.tasks._engine", lambda: database)
    with Session(database) as session:
        session.add(ProjectDB(
            tenant_id=command.tenant_id, project_id="project-1", name="Synthetic project",
            created_by_subject_id="synthetic-test-subject",
        ))
        session.add(TaskDB(
            id=command.control_task_id, tenant_id=command.tenant_id, project_id="project-1", status="running",
        ))
        session.commit()
    repository = FakeRepository()
    queue = FakeQueue(repository)
    monkeypatch.setattr("agent.repository.task_repo", repository)
    monkeypatch.setattr("agent.services.task_queue_service.get_task_queue_service", lambda: queue)
    monkeypatch.setattr(
        "agent.services.pi_task_scope_composition.get_repository_registry",
        lambda: SimpleNamespace(task_repo=TaskRepository()),
    )
    yield SimpleNamespace(
        database=database, command=command, queue=queue, repository=repository,
        adapter=build_native_graph_task_queue_adapter(),
    )
    database.dispose()


def test_production_pi_submission_inherits_project_without_requiring_context_bundle(scoped_submission):
    f = scoped_submission
    receipt = f.adapter.submit(f.command)
    task = f.repository.get_by_id(receipt.hub_task_id)
    assert task.tenant_id == f.command.tenant_id and task.project_id == "project-1"
    assert task.parent_task_id == f.command.control_task_id
    assert not hasattr(task, "context_bundle_id")
    assert task.worker_execution_context["native_node_command"] == f.command.to_dict()
    assert len(f.queue.ingested_values) == 1


@pytest.mark.parametrize("change", [
    "missing_parent", "foreign_tenant", "missing_project_scope", "malformed_scope", "terminal_parent",
    "archived_project", "missing_project", "foreign_project_tenant",
])
def test_pi_submission_rejects_missing_foreign_or_inactive_scope(scoped_submission, change):
    f = scoped_submission
    with Session(f.database) as session:
        task = session.get(TaskDB, f.command.control_task_id)
        project = session.get(ProjectDB, (f.command.tenant_id, "project-1"))
        if change == "missing_parent":
            session.delete(task)
        elif change == "foreign_tenant":
            task.tenant_id = "foreign"
        elif change == "missing_project_scope":
            task.project_id = None
        elif change == "malformed_scope":
            task.project_id = " project-1 "
        elif change == "terminal_parent":
            task.status = "completed"
        elif change == "archived_project":
            project.status = "archived"
        elif change == "missing_project":
            session.delete(project)
        else:
            project.tenant_id = "foreign"
        session.commit()
    with pytest.raises(ValueError, match="^(pi_task_scope_|native_context_control_task_)"):
        f.adapter.submit(f.command)
    assert not f.queue.ingested_values


def test_pi_submission_never_merges_different_context_and_project_snapshots(scoped_submission):
    f = scoped_submission
    command = replace(f.command, node=replace(f.command.node, metadata={
        "context_bundle_mode": "control_task", "context_policy_id": "policy-1",
        "context_destination_id": "destination-1",
    }))
    f.adapter._context_preparer = SimpleNamespace(prepare=lambda **_: SimpleNamespace(
        scope={"tenant_id": command.tenant_id, "project_id": "changed-project"},
        parent_task_id=command.control_task_id, bundle_id="synthetic-bundle",
    ))
    with pytest.raises(ValueError, match="^pi_task_scope_changed_before_ingestion$"):
        f.adapter.submit(command)
    assert not f.queue.ingested_values
