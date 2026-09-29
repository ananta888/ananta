import json
import os
import time

import pytest

from agent.ai_agent import create_app
from agent.config import settings
from agent.utils import _archive_old_tasks


@pytest.fixture
def app():
    app = create_app(agent="test-agent")
    tasks_path = "tests/test_tasks_archiving.json"
    app.config["TASKS_PATH"] = tasks_path
    app.config["AGENT_CONFIG"] = {}
    app.config["TESTING"] = True

    if os.path.exists(tasks_path):
        try:
            os.remove(tasks_path)
        except PermissionError:
            pass
    archive_path = tasks_path.replace(".json", "_archive.json")
    if os.path.exists(archive_path):
        try:
            os.remove(archive_path)
        except PermissionError:
            pass

    with app.app_context():
        yield app

    if os.path.exists(tasks_path):
        try:
            os.remove(tasks_path)
        except PermissionError:
            pass
    if os.path.exists(archive_path):
        try:
            os.remove(archive_path)
        except PermissionError:
            pass


def test_archive_old_tasks_json(app):
    with app.app_context():
        # Settings manipulieren für den Test
        settings.tasks_retention_days = 1  # 1 Tag

        now = time.time()
        old_time = now - (2 * 86400)  # 2 Tage alt

        # 1. Tasks erstellen
        # Wir übergeben den Pfad explizit, um die JSON-Logik zu testen
        tasks_path = app.config["TASKS_PATH"]
        tasks = {
            "old_task": {"id": "old_task", "status": "completed", "created_at": old_time},
            "new_task": {"id": "new_task", "status": "started", "created_at": now},
        }
        with open(tasks_path, "w") as f:
            json.dump(tasks, f)

        # 2. Archivierung ausführen (expliziter Pfad triggert JSON Logik)
        _archive_old_tasks(tasks_path=tasks_path)

        # 3. Prüfen
        with open(tasks_path, "r") as f:
            remaining_tasks = json.load(f)

        assert "new_task" in remaining_tasks
        assert "old_task" not in remaining_tasks

        archive_path = tasks_path.replace(".json", "_archive.json")
        assert os.path.exists(archive_path)
        with open(archive_path, "r") as f:
            archived_tasks = json.load(f)

        assert "old_task" in archived_tasks
        assert archived_tasks["old_task"]["status"] == "completed"


def test_archive_old_tasks_db(app):
    from agent.db_models import TaskDB
    from agent.repository import task_repo

    with app.app_context():
        settings.tasks_retention_days = 1
        now = time.time()
        old_time = now - (2 * 86400)

        # 1. Tasks in DB erstellen
        old_task = TaskDB(id="old_db_task", created_at=old_time, status="completed")
        new_task = TaskDB(id="new_db_task", created_at=now, status="todo")
        task_repo.save(old_task)
        task_repo.save(new_task)

        # 2. Archivierung ausführen (ohne Pfad -> DB Logik)
        _archive_old_tasks()

        # 3. Prüfen
        assert task_repo.get_by_id("new_db_task") is not None
        assert task_repo.get_by_id("old_db_task") is None


def test_archive_old_tasks_retains_active_and_recovery_lineage(app):
    from agent.db_models import TaskDB
    from agent.repository import task_repo

    with app.app_context():
        settings.tasks_retention_days = 1
        old_time = time.time() - (2 * 86400)
        task_repo.save(
            TaskDB(
                id="old-active-task",
                created_at=old_time,
                status="todo",
            )
        )
        task_repo.save(
            TaskDB(
                id="old-recovery-child",
                created_at=old_time,
                status="cancelled",
                source_task_id="recovery-source",
                derivation_reason="goal_task_recovery",
            )
        )

        _archive_old_tasks()

        assert task_repo.get_by_id("old-active-task") is not None
        assert task_repo.get_by_id("old-recovery-child") is not None


def test_archive_old_tasks_retains_terminal_dependency_of_active_task(app):
    from agent.db_models import TaskDB
    from agent.repository import task_repo

    with app.app_context():
        settings.tasks_retention_days = 1
        old_time = time.time() - (2 * 86400)
        task_repo.save(
            TaskDB(
                id="old-live-dependency",
                created_at=old_time,
                status="completed",
            )
        )
        task_repo.save(
            TaskDB(
                id="active-dependent",
                status="blocked_by_dependency",
                depends_on=["old-live-dependency"],
            )
        )

        _archive_old_tasks()

        assert task_repo.get_by_id("old-live-dependency") is not None


def test_archive_retention_preserves_archived_recovery_lineage(app):
    from agent.db_models import ArchivedTaskDB
    from agent.repository import archived_task_repo

    with app.app_context():
        settings.archived_tasks_retention_days = 1
        old_time = time.time() - (2 * 86400)
        archived_task_repo.save(
            ArchivedTaskDB(
                id="old-archived-recovery-child",
                created_at=old_time,
                updated_at=old_time,
                archived_at=old_time,
                status="cancelled",
                source_task_id="archived-recovery-source",
                derivation_reason="goal_task_recovery",
            )
        )

        _archive_old_tasks()

        assert (
            archived_task_repo.get_by_id(
                "old-archived-recovery-child"
            )
            is not None
        )


def test_json_archive_retention_preserves_recovery_lineage(app):
    with app.app_context():
        settings.archived_tasks_retention_days = 1
        tasks_path = app.config["TASKS_PATH"]
        archive_path = tasks_path.replace(".json", "_archive.json")
        old_time = time.time() - (2 * 86400)
        with open(tasks_path, "w", encoding="utf-8") as tasks_file:
            json.dump({}, tasks_file)
        with open(
            archive_path,
            "w",
            encoding="utf-8",
        ) as archive_file:
            json.dump(
                {
                    "old-json-recovery": {
                        "id": "old-json-recovery",
                        "status": "cancelled",
                        "created_at": old_time,
                        "archived_at": old_time,
                        "source_task_id": "json-source",
                        "derivation_reason": (
                            "goal_task_recovery"
                        ),
                    }
                },
                archive_file,
            )

        _archive_old_tasks(tasks_path=tasks_path)

        with open(
            archive_path,
            encoding="utf-8",
        ) as archive_file:
            archived = json.load(archive_file)
        assert "old-json-recovery" in archived
