"""Real competing Hub navigation CAS; no Worker-owned queue or lost updates."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from tests.test_meet_browser_hub_tasks import navigate, setup


@pytest.mark.timeout(30)
def test_competing_navigation_commands_create_only_the_cas_winners_child(app):
    from agent.services.task_runtime_service import compare_and_set_local_task_status

    with app.app_context():
        fixture = setup()
        before = fixture.tasks.get_by_id(fixture.task_id).worker_execution_context
    barrier = Barrier(2)

    def racing_cas(task_id, *args, **kwargs):
        if task_id == fixture.task_id:
            barrier.wait(timeout=5)
        return compare_and_set_local_task_status(task_id, *args, **kwargs)

    fixture.browser_tasks.compare_and_set = racing_cas
    fixture.browser_tasks.create = Mock(wraps=fixture.browser_tasks.create)

    def execute():
        with app.app_context():
            try:
                _, receipt = navigate(fixture)
                return receipt["task_id"]
            except MeetError as error:
                assert error.code == "meet_browser_control_conflict" and error.status == 409
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(execute) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    fixture.browser_tasks.create.assert_called_once()
    with app.app_context():
        state = fixture.browser_tasks.read(fixture.scope)
        assert state["revision"] == 2 and state["job"]["task_id"] == winners[0]
        assert fixture.tasks.get_by_id(winners[0]).status == "in_progress"
        after = fixture.tasks.get_by_id(fixture.task_id).worker_execution_context
        assert {key: value for key, value in after.items() if key != "meet_browser"} == before
