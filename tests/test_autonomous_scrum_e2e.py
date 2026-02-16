import json

from agent.config import settings
from agent.db_models import AgentInfoDB, ConfigDB
from agent.repository import agent_repo, config_repo
from agent.routes.tasks.autopilot import AUTOPILOT_STATE_KEY, AutonomousLoopManager, autonomous_loop
from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status


def test_e2e_autonomous_scrum_progress_with_followup_chain(app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    autonomous_loop.stop(persist=False)

    with app.app_context():
        agent_repo.save(
            AgentInfoDB(
                url="http://worker-e2e:5000",
                name="worker-e2e",
                role="worker",
                token="tok-e2e",
                status="online",
            )
        )
        _update_local_task_status("E2E-PARENT", "todo", title="Parent", team_id="team-e2e")
        _update_local_task_status(
            "E2E-CHILD",
            "blocked",
            title="Child",
            team_id="team-e2e",
            parent_task_id="E2E-PARENT",
        )

        def _fake_forward(worker_url, endpoint, data, token=None):
            if endpoint.endswith("/step/propose"):
                return {"status": "success", "data": {"reason": "work_on_task", "command": "echo ok"}}
            if endpoint.endswith("/step/execute"):
                return {"status": "success", "data": {"status": "completed", "exit_code": 0, "output": "execution success ok"}}
            raise AssertionError(f"unexpected endpoint: {endpoint}")

        monkeypatch.setattr("agent.routes.tasks.autopilot._forward_to_worker", _fake_forward)

        # Tick 1: Parent wird erledigt, Child bleibt noch blocked.
        autonomous_loop.tick_once()
        # Tick 2: Child wird entsperrt und abgearbeitet.
        autonomous_loop.tick_once()

        parent = _get_local_task_status("E2E-PARENT")
        child = _get_local_task_status("E2E-CHILD")

    assert parent["status"] == "completed"
    assert child["status"] == "completed"
    assert any((h.get("event_type") == "autopilot_result") for h in (parent.get("history") or []))
    assert any((h.get("event_type") == "dependency_unblocked") for h in (child.get("history") or []))


def test_e2e_autonomous_scrum_recovery_restore_restarts_enabled_loop(app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    autonomous_loop.stop(persist=False)

    with app.app_context():
        config_repo.save(
            ConfigDB(
                key=AUTOPILOT_STATE_KEY,
                value_json=json.dumps(
                    {
                        "enabled": True,
                        "interval_seconds": 7,
                        "max_concurrency": 3,
                        "tick_count": 2,
                    }
                ),
            )
        )

        manager = AutonomousLoopManager()
        calls = {}

        def _fake_start(interval_seconds=None, max_concurrency=None, persist=True, background=True):
            calls["interval_seconds"] = interval_seconds
            calls["max_concurrency"] = max_concurrency
            calls["persist"] = persist
            calls["background"] = background

        monkeypatch.setattr(manager, "start", _fake_start)
        manager.restore()

    assert calls["interval_seconds"] == 7
    assert calls["max_concurrency"] == 3
    assert calls["persist"] is False
