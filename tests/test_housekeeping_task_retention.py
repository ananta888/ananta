from __future__ import annotations

from types import SimpleNamespace


def test_task_retention_runs_only_on_hub(monkeypatch) -> None:
    from agent.services.background import housekeeping

    calls: list[str] = []
    app = SimpleNamespace(config={"TASKS_PATH": "/tmp/tasks.json"})
    monkeypatch.setattr(
        housekeeping,
        "_archive_old_tasks",
        lambda path: calls.append(path),
    )

    monkeypatch.setattr(housekeeping.settings, "role", "worker")
    housekeeping._archive_old_tasks_if_hub(app)
    assert calls == []

    monkeypatch.setattr(housekeeping.settings, "role", "hub")
    housekeeping._archive_old_tasks_if_hub(app)
    assert calls == ["/tmp/tasks.json"]
