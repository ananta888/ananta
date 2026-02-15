import time

from agent.db_models import TaskDB
from agent.routes.tasks import utils as task_utils


def test_get_local_task_status_returns_none_when_missing(monkeypatch):
    class StubRepo:
        @staticmethod
        def get_by_id(_tid):
            return None

    monkeypatch.setattr(task_utils, "task_repo", StubRepo())
    assert task_utils._get_local_task_status("T-missing") is None


def test_update_local_task_status_creates_and_saves_task(monkeypatch):
    saved = {}
    notified = []

    class StubRepo:
        @staticmethod
        def get_by_id(_tid):
            return None

        @staticmethod
        def save(task):
            saved["task"] = task
            return task

    monkeypatch.setattr(task_utils, "task_repo", StubRepo())
    monkeypatch.setattr(task_utils, "_notify_task_update", lambda tid: notified.append(tid))

    task_utils._update_local_task_status("T-1", "in_progress", title="Test task")

    task = saved["task"]
    assert isinstance(task, TaskDB)
    assert task.id == "T-1"
    assert task.status == "in_progress"
    assert task.title == "Test task"
    assert task.updated_at <= time.time()
    assert notified == ["T-1"]


def test_forward_to_worker_builds_url_and_auth_header(monkeypatch):
    calls = []

    def fake_http_post(url, data=None, headers=None):
        calls.append({"url": url, "data": data, "headers": headers or {}})
        return {"ok": True}

    monkeypatch.setattr(task_utils, "_http_post", fake_http_post)

    result = task_utils._forward_to_worker(
        "http://worker.local/",
        "/step/execute",
        {"command": "echo hi"},
        token="tok-123",
    )

    assert result == {"ok": True}
    assert calls[0]["url"] == "http://worker.local/step/execute"
    assert calls[0]["data"] == {"command": "echo hi"}
    assert calls[0]["headers"]["Authorization"] == "Bearer tok-123"

