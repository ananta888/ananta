import time
from unittest.mock import MagicMock

from agent.db_models import ScheduledTaskDB
from agent import scheduler as scheduler_module


def test_add_task_persists_and_appends(monkeypatch):
    class StubRepo:
        @staticmethod
        def get_all():
            return []

        @staticmethod
        def save(task):
            return task

        @staticmethod
        def delete(_task_id):
            return True

    monkeypatch.setattr(scheduler_module, "scheduled_task_repo", StubRepo())
    scheduler = scheduler_module.TaskScheduler()

    task = scheduler.add_task("echo hello", interval_seconds=5)
    assert task.command == "echo hello"
    assert task.interval_seconds == 5
    assert task in scheduler.tasks
    assert task.next_run > time.time()


def test_remove_task_updates_in_memory_list(monkeypatch):
    t1 = ScheduledTaskDB(id="A", command="echo a", interval_seconds=1, next_run=time.time() + 1)
    t2 = ScheduledTaskDB(id="B", command="echo b", interval_seconds=1, next_run=time.time() + 1)

    class StubRepo:
        @staticmethod
        def get_all():
            return [t1, t2]

        @staticmethod
        def save(task):
            return task

        @staticmethod
        def delete(task_id):
            return task_id == "A"

    monkeypatch.setattr(scheduler_module, "scheduled_task_repo", StubRepo())
    scheduler = scheduler_module.TaskScheduler()
    scheduler.remove_task("A")

    assert [t.id for t in scheduler.tasks] == ["B"]


def test_execute_task_uses_shell_pool_and_updates_schedule(monkeypatch):
    task = ScheduledTaskDB(id="T-run", command="echo run", interval_seconds=10, next_run=time.time())
    saved = {}

    class StubRepo:
        @staticmethod
        def get_all():
            return []

        @staticmethod
        def save(t):
            saved["task"] = t
            return t

        @staticmethod
        def delete(_task_id):
            return True

    shell = MagicMock()
    shell.execute.return_value = ("ok", 0)
    pool = MagicMock()
    pool.acquire.return_value = shell

    monkeypatch.setattr(scheduler_module, "scheduled_task_repo", StubRepo())
    monkeypatch.setattr(scheduler_module, "get_shell_pool", lambda: pool)

    scheduler = scheduler_module.TaskScheduler()
    scheduler.running_task_ids.add(task.id)
    old_next_run = task.next_run

    scheduler._execute_task(task)

    pool.acquire.assert_called_once()
    pool.release.assert_called_once_with(shell)
    shell.execute.assert_called_once_with("echo run")
    assert "task" in saved
    assert saved["task"].last_run is not None
    assert saved["task"].next_run > old_next_run
    assert task.id not in scheduler.running_task_ids

