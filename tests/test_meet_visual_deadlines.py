"""Orphan cleanup uses the real Hub TaskQueue/CAS, never a manufactured Worker result."""

from copy import deepcopy

import pytest

from agent.repositories.meet_dialog_deadlines import SqlDialogDeadlines
from agent.services.meet_dialog_deadlines import MeetDialogDeadlines, original_deadline
from tests.meet_visual_fixture import visual_system
from tests.test_meet_visual_receive import visual_fixture

pytestmark = pytest.mark.timeout(45)


def candidate():
    job, assignment, _ = visual_fixture()
    return {
        "task_id": job["task_id"],
        "task_kind": "meet_visual_receive",
        "tenant_id": assignment["tenant_id"],
        "project_id": assignment["project_id"],
        "parent_task_id": assignment["task_id"],
        "context": {"meet_visual_job": job, "parent_dispatch": "dispatch", "runtime_id": assignment["runtime_id"]},
    }


def test_real_orphan_visual_child_is_scanned_and_settled_once_at_original_deadline(app):
    from agent.database import engine
    from agent.services.task_runtime_service import compare_and_set_local_task_status

    with app.app_context():
        f = visual_system()
        job = f.service.visual(f.payload | {"publication_id": "camera"})["job"]
        before = deepcopy(f.tasks.get_by_id(job["task_id"]).worker_execution_context)
        store = SqlDialogDeadlines(engine, task_status_cas=compare_and_set_local_task_status)
        rows = {row["task_id"]: row for row in store.page(None, 100)}
        assert job["task_id"] in rows, "orphan visual child is missing from the independent Hub scanner"
        assert original_deadline(rows[job["task_id"]]) == job["deadline"]
        assert MeetDialogDeadlines(store, clock=lambda: job["deadline"] - 0.01).run_once()["settled"] == 0
        assert f.tasks.get_by_id(job["task_id"]).status == "in_progress"
        assert MeetDialogDeadlines(store, clock=lambda: job["deadline"]).run_once()["settled"] == 1
        child = f.tasks.get_by_id(job["task_id"])
        assert child.status == "failed" and child.worker_execution_context == before
        events = [entry for entry in child.history if entry.get("event_type") == "meet_visual_deadline_expired"]
        assert len(events) == 1 and events[0]["details"] == {"reason": "original_deadline_expired"}
        assert f.tasks.get_by_id(f.task_id).status == "in_progress"
        snapshot = child.model_dump()
        assert MeetDialogDeadlines(store, clock=lambda: job["deadline"] + 1).run_once()["settled"] == 0
        assert not store.settle(rows[job["task_id"]], lambda: True)
        assert f.tasks.get_by_id(job["task_id"]).model_dump() == snapshot
        assert f.worker.start_dialog.call_count == 1
        f.service.media_worker.execute.assert_not_called()


@pytest.mark.parametrize(
    "change", ["child", "parent", "self_parent", "dispatch", "runtime", "extra", "missing", "profile", "deadline"]
)
def test_visual_deadline_rejects_malformed_persisted_binding(change):
    row = candidate()
    if change == "child":
        row["task_id"] = "other"
    elif change == "parent":
        row["parent_task_id"] = ""
    elif change == "self_parent":
        row["parent_task_id"] = row["task_id"]
    elif change in {"dispatch", "runtime"}:
        row["context"]["parent_dispatch" if change == "dispatch" else "runtime_id"] = None
    elif change == "extra":
        row["context"]["arbitrary"] = True
    elif change == "missing":
        del row["context"]["meet_visual_job"]["lease_id"]
    elif change == "profile":
        row["context"]["meet_visual_job"]["profile"] = "unbounded"
    else:
        row["context"]["meet_visual_job"]["deadline"] = True
    with pytest.raises(ValueError):
        original_deadline(row)


def test_visual_deadline_validates_expired_shape_without_reauthorizing_a_run():
    row = candidate()
    row["context"]["meet_visual_job"].update(issued_at=80, deadline=100)
    assert original_deadline(row) == 100
