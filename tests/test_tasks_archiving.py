import os
import json
import time
import pytest
from flask import Flask
from agent.routes.tasks import _archive_old_tasks, _update_local_task_status, _get_tasks_cache
from agent.config import settings

@pytest.fixture
def app():
    app = Flask(__name__)
    tasks_path = "tests/test_tasks_archiving.json"
    app.config["TASKS_PATH"] = tasks_path
    app.config["AGENT_CONFIG"] = {}
    
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

def test_archive_old_tasks(app):
    with app.app_context():
        # Settings manipulieren für den Test
        settings.tasks_retention_days = 1 # 1 Tag
        
        now = time.time()
        old_time = now - (2 * 86400) # 2 Tage alt
        
        # 1. Tasks erstellen
        # Wir nutzen _update_local_task_status, aber wir müssen die Zeit manipulieren
        _update_local_task_status("old_task", "completed")
        _update_local_task_status("new_task", "started")
        
        # Zeitstempel manuell anpassen
        tasks_path = app.config["TASKS_PATH"]
        with open(tasks_path, "r") as f:
            tasks = json.load(f)
        
        tasks["old_task"]["created_at"] = old_time
        tasks["new_task"]["created_at"] = now
        
        with open(tasks_path, "w") as f:
            json.dump(tasks, f)
            
        # 2. Archivierung ausführen
        _archive_old_tasks()
        
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
