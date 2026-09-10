import json
import threading
import time
from pathlib import Path
from queue import Queue

import pytest

from agent.ai_agent import create_app
from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status


@pytest.fixture
def app(tmp_path):
    app = create_app(agent="test-agent")
    app.config["TASKS_PATH"] = str(tmp_path / "tasks.json")
    app.config["TESTING"] = True

    with app.app_context():
        yield app


def test_task_cache_consistency(app, tmp_path):
    assert Path(app.config["TASKS_PATH"]).parent == tmp_path
    with app.app_context():
        # 1. Update Task
        _update_local_task_status("t1", "created", title="test task")

        # 2. Check Cache
        status = _get_local_task_status("t1")
        assert status is not None
        assert status["status"] == "created"
        assert status["title"] == "test task"

        # 3. Direkt in DB schreiben (simuliert anderen Prozess)
        from agent.repository import task_repo

        task = task_repo.get_by_id("t1")
        task.status = "updated"
        task_repo.save(task)

        # 4. Check Cache
        status = _get_local_task_status("t1")
        assert status["status"] == "updated"


def test_atomic_updates(app, tmp_path):
    assert Path(app.config["TASKS_PATH"]).parent == tmp_path
    with app.app_context():
        num_threads = 10
        num_updates = 5
        errors = Queue()

        def update_worker():
            try:
                for _ in range(num_updates):

                    def updater(data):
                        val = data.get("counter", 0)
                        data["counter"] = val + 1
                        return data

                    from agent.utils import update_json

                    update_json(app.config["TASKS_PATH"], updater, default={})
            except Exception as exc:
                errors.put(type(exc).__name__)

        threads = []
        for _ in range(num_threads):
            t = threading.Thread(target=update_worker, daemon=True)
            threads.append(t)
            t.start()

        deadline = time.monotonic() + 10
        for t in threads:
            t.join(timeout=max(0, deadline - time.monotonic()))
        assert not any(t.is_alive() for t in threads), "JSON update threads exceeded the headless deadline"
        assert errors.empty(), "JSON update thread failed"

        # Prüfen ob Counter korrekt ist (sollte num_threads * num_updates sein)
        with open(app.config["TASKS_PATH"], "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["counter"] == num_threads * num_updates
