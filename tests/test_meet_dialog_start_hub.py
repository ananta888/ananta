"""Real Hub task queue and role authorization with a synthetic publisher port."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlmodel import Session, select

from agent.db_models import TaskDB
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_starts import MeetDialogStarts
from tests.meet_dialog_lifecycle_fixture import seed_parent
from tests.test_meet_dialog_avatar_negotiation import system
from tests.test_meet_dialog_starts import start_store as start_store

pytestmark = pytest.mark.timeout(45)


@pytest.mark.parametrize("parented", [False, True])
def test_identical_command_creates_one_hub_task_and_never_revives_stopped_task(app, store, parented):
    from agent.database import engine

    with app.app_context():
        if parented:
            seed_parent(engine)
        f = system()
        coordinator = MeetDialogStarts(f.service, store, f.f.binding)
        parent = "meet-test-parent" if parented else ""
        first, replayed = coordinator.start(f.principal, "project", f.payload, parent, "stable")
        assert replayed is False
        repeated, replayed = coordinator.start(f.principal, "project", f.payload, parent, "stable")
        assert repeated == first and replayed is True
        assert f.worker.start_dialog.call_count == 1 and f.issuer.issue_dialog.call_count == 1
        task = f.tasks.get_by_id(first["task_id"])
        context = task.worker_execution_context["meet_dialog"]
        assert f.tasks.finish_bound(task.id, context["lease_id"], context["runtime_id"], "cancelled")
        assert coordinator.start(f.principal, "project", f.payload, parent, "stable") == (first, True)
        assert f.tasks.get_by_id(task.id).status == "cancelled"
        assert f.worker.start_dialog.call_count == 1
        with Session(engine) as session:
            tasks = session.exec(select(TaskDB).where(TaskDB.task_kind == "meet_dialog_session")).all()
            assert len(tasks) == 1 and tasks[0].id == first["task_id"]


def test_concurrent_retry_while_original_worker_handshake_is_pending_does_not_dispatch(app, store):
    entered, release = Event(), Event()
    with app.app_context():
        f = system()
    coordinator = MeetDialogStarts(f.service, store, f.f.binding)

    def hold(_assignment):
        entered.set()
        assert release.wait(5), "bounded synthetic Worker handshake not released"

    f.worker.start_dialog.side_effect = hold

    def start():
        with app.app_context():
            return coordinator.start(f.principal, "project", f.payload, "", "concurrent")

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(start)
        try:
            assert entered.wait(5), "first authorized Hub dispatch missing"
            with pytest.raises(MeetError, match="^meet_dialog_start_pending$"):
                start()
            assert f.worker.start_dialog.call_count == 1
        finally:
            release.set()
        result = future.result(timeout=5)
    assert start() == (result[0], True)
    assert f.worker.start_dialog.call_count == 1
