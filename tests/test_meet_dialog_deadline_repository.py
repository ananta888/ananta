"""Actual isolated Hub TaskQueue and SQL CAS; no release evidence or live DB."""

import uuid
from copy import deepcopy

import pytest

from agent.repositories.meet_dialog_deadlines import SqlDialogDeadlines
from agent.services.meet_dialog_deadlines import MeetDialogDeadlines
from tests.test_meet_dialog_deadlines import candidate

pytestmark = pytest.mark.timeout(45)


@pytest.fixture(autouse=True)
def owning_project(app):
    from sqlmodel import Session

    from agent.database import engine
    from agent.db_models.projects import ProjectDB

    # Every newly opened SQLite connection must see the actual scope FK parent.
    with app.app_context(), Session(engine) as session:
        session.merge(
            ProjectDB(
                tenant_id="synthetic-tenant",
                project_id="synthetic-project",
                name="Synthetic deadline checks",
                created_by_subject_id="synthetic-owner",
            )
        )
        session.commit()


def ingest(row, status="in_progress"):
    from agent.services.task_queue_service import get_task_queue_service

    get_task_queue_service().ingest_task(
        task_id=row["task_id"],
        status=status,
        title="Synthetic deadline fixture",
        source="meet_dialog",
        created_by="synthetic-owner",
        extra_fields={
            "task_kind": row["task_kind"],
            "tenant_id": row["tenant_id"],
            "project_id": row["project_id"],
            "parent_task_id": row["parent_task_id"],
            "worker_execution_context": row["context"],
        },
    )


def sql_store(engine):
    from agent.services.task_runtime_service import compare_and_set_local_task_status

    return SqlDialogDeadlines(engine, task_status_cas=compare_and_set_local_task_status)


def test_actual_queue_settles_expired_parent_and_orphan_child_once(app):
    from agent.database import engine
    from agent.services.meet_dialog_tasks import HubDialogTasks

    prefix = str(uuid.uuid4())
    rows = [candidate(prefix + str(n), 100 if n < 2 else 200, audio=n == 1) for n in range(5)]
    rows[1]["parent_task_id"] = rows[0]["task_id"]
    rows[3]["task_kind"] = "unrelated_task"
    rows[4]["context"]["meet_dialog"]["deadline"] = None
    with app.app_context():
        for row in rows:
            ingest(row)
        store = sql_store(engine)
        snapshot = store.page(None, 100)
        relevant = {r["task_id"]: r for r in snapshot if r["task_id"].startswith(prefix)}
        assert rows[3]["task_id"] not in relevant
        parent, child = relevant[rows[0]["task_id"]], relevant[rows[1]["task_id"]]
        assert store.settle(parent, lambda: True)
        # Simulate a Hub restart after only parent settlement: independently scan
        # the child; neither a parent callback nor an in-memory parent is needed.
        runner = MeetDialogDeadlines(sql_store(engine), clock=lambda: 100)
        for _ in range(100):
            runner.run_once(limit=2)
            if runner.cursor is None:
                break
        else:
            pytest.fail("bounded fixture scan did not reach EOF")
        assert not store.settle(child, lambda: True)
        tasks = HubDialogTasks()
        assert [tasks.get_by_id(r["task_id"]).status for r in rows] == [
            "failed",
            "failed",
            "in_progress",
            "in_progress",
            "in_progress",
        ]
        for row, event_type in ((parent, "meet_dialog_deadline_expired"), (child, "meet_audio_deadline_expired")):
            events = [e for e in tasks.get_by_id(row["task_id"]).history if e.get("event_type") == event_type]
            assert len(events) == 1
            assert events[0]["details"] == {"reason": "original_deadline_expired"}


@pytest.mark.parametrize(
    "change", ["tenant", "project", "kind", "parent", "dispatch", "runtime", "deadline", "terminal", "clock"]
)
def test_captured_row_cannot_settle_changed_assignment_or_terminal_task(app, change):
    from agent.database import engine
    from agent.services.meet_dialog_tasks import HubDialogTasks
    from agent.services.task_runtime_service import compare_and_set_local_task_status

    row = candidate(str(uuid.uuid4()))
    with app.app_context():
        ingest(row)
        store = sql_store(engine)
        snapshot = next(r for r in store.page(None, 100) if r["task_id"] == row["task_id"])
        updates = {}
        if change in {"tenant", "project", "kind", "parent"}:
            field = {"tenant": "tenant_id", "project": "project_id", "kind": "task_kind", "parent": "parent_task_id"}[
                change
            ]
            # An out-of-scope captured projection must not touch the actual row.
            snapshot[field] = "foreign"
        elif change in {"dispatch", "runtime", "deadline"}:
            context = deepcopy(row["context"])
            key = {"dispatch": "lease_id", "runtime": "runtime_id", "deadline": "deadline"}[change]
            context["meet_dialog"][key] = 200 if change == "deadline" else "replacement"
            updates["worker_execution_context"] = context
        if updates or change == "terminal":
            assert compare_and_set_local_task_status(
                row["task_id"],
                "completed" if change == "terminal" else "in_progress",
                expected_statuses={"in_progress"},
                **updates,
            )
        if change == "kind":
            with pytest.raises(ValueError):
                store.settle(snapshot, lambda: True)
        else:
            assert not store.settle(snapshot, lambda: change != "clock")
        assert HubDialogTasks().get_by_id(row["task_id"]).status == (
            "completed" if change == "terminal" else "in_progress"
        )
