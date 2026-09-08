"""Owned abrupt-crash injection and real-clock native Hub reconciliation."""

import json
import re
import time


def owned_worker_id(container):
    """Resolve one freshly created fixture before any destructive test injection."""
    if not container.created or not re.fullmatch(r"meet-test-dialog-worker-[a-f0-9-]{36}", container.name):
        raise ValueError("test_worker_crash_target_invalid")
    rows = json.loads(container.command("inspect", container.name))
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError("test_worker_crash_target_invalid")
    row = rows[0]
    if (
        not re.fullmatch(r"[a-f0-9]{64}", row.get("Id", ""))
        or row.get("Name") != "/" + container.name
        or row.get("Image") != container.image
        or row.get("State", {}).get("Running") is not True
        or set(row.get("NetworkSettings", {}).get("Networks", {})) != {container.network}
    ):
        raise ValueError("test_worker_crash_target_invalid")
    return row["Id"]


def crash_owned_worker(container):
    identifier = owned_worker_id(container)
    container.command("kill", "--signal=KILL", identifier)
    state = json.loads(container.command("inspect", identifier, "--format", "{{json .State}}"))
    assert state["Running"] is False and state["ExitCode"] == 137, "owned Worker did not exit abruptly"
    container.test_crashed = True


def reconcile_crashed_worker(app, tasks, task_id, record_property, *, container_killed=True):
    """No clock substitution, task mutation, fake callback or assignment replay."""
    from agent.database import engine
    from agent.repositories.meet_dialog_deadlines import SqlDialogDeadlines
    from agent.services.meet_dialog_deadlines import MeetDialogDeadlines
    from agent.services.task_runtime_service import compare_and_set_local_task_status

    with app.app_context():
        original = tasks.get_by_id(task_id)
        context = original.worker_execution_context
        deadline = context["meet_dialog"]["deadline"]
    remaining = deadline - time.time()
    assert -5 <= remaining <= 125, "crash fixture exceeded its closed original-deadline scope"
    until = time.monotonic() + max(0, remaining) + 8
    runner = MeetDialogDeadlines(SqlDialogDeadlines(engine, task_status_cas=compare_and_set_local_task_status))
    while time.monotonic() < until:
        with app.app_context():
            runner.run_once(limit=100)
            current = tasks.get_by_id(task_id)
        if current.status != "in_progress":
            break
        time.sleep(0.1)
    assert current.status == "failed", "original-deadline reconciliation did not settle the crashed Worker"
    assert current.worker_execution_context == context, "reconciliation changed the original assignment"
    assert time.time() >= deadline, "crashed task was settled before its original deadline"
    events = [event for event in current.history if event.get("event_type") == "meet_dialog_deadline_expired"]
    assert len(events) == 1 and events[0]["details"] == {"reason": "original_deadline_expired"}
    snapshot = current.model_dump()
    with app.app_context():
        # A fresh coordinator has no remembered cursor or completed callback.
        MeetDialogDeadlines(SqlDialogDeadlines(engine, task_status_cas=compare_and_set_local_task_status)).run_once(
            limit=100
        )
        assert tasks.get_by_id(task_id).model_dump() == snapshot
    record_property(
        "packaged_worker_crash",
        {
            "abrupt_container_exit": 137 if container_killed else None,
            "runtime_stall": not container_killed,
            "surviving_worker_continues": True,
            "original_deadline_settled_once": True,
            "real_clock": True,
            "worker_finish_callback": False,
            "assignment_replayed": False,
            "synthetic_policy": True,
            "production_release_evidence": False,
        },
    )
