import os
import json
import time
import pytest
from agent.ai_agent import create_app
from agent.routes.tasks.utils import _update_local_task_status, _get_tasks_cache
from agent.utils import _archive_old_tasks
from agent.config import settings

@pytest.fixture
def app():
    app = create_app(agent="test-agent")
    tasks_path = "tests/test_tasks_archiving.json"
    app.config["TASKS_PATH"] = tasks_path
    app.config["AGENT_CONFIG"] = {}
    app.config["TESTING"] = True
    
    if os.path.exists(tasks_path):
        os.remove(tasks_path)
    archive_path = tasks_path.replace(".json", "_archive.json")
    if os.path.exists(archive_path):
        os.remove(archive_path)
        
    with app.app_context():
        yield app
    
    if os.path.exists(tasks_path):
        os.remove(tasks_path)
    if os.path.exists(archive_path):
        os.remove(archive_path)

def test_archive_old_tasks_json(app):
    with app.app_context():
        # Settings manipulieren für den Test
        settings.tasks_retention_days = 1 # 1 Tag
        
        now = time.time()
        old_time = now - (2 * 86400) # 2 Tage alt
        
        # 1. Tasks erstellen
        # Wir übergeben den Pfad explizit, um die JSON-Logik zu testen
        tasks_path = app.config["TASKS_PATH"]
        tasks = {
            "old_task": {
                "id": "old_task",
                "status": "completed",
                "created_at": old_time
            },
            "new_task": {
                "id": "new_task",
                "status": "started",
                "created_at": now
            }
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
    from agent.repository import task_repo
    from agent.db_models import TaskDB
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
