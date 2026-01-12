import os
import json
import time
import pytest
import threading
from agent.routes.tasks import _update_local_task_status, _get_local_task_status, _get_tasks_cache
from flask import Flask

@pytest.fixture
def app():
    app = Flask(__name__)
    tasks_path = "tests/test_tasks.json"
    app.config["TASKS_PATH"] = tasks_path
    if os.path.exists(tasks_path):
        os.remove(tasks_path)
    
    with app.app_context():
        yield app
    
    if os.path.exists(tasks_path):
        os.remove(tasks_path)

def test_task_cache_consistency(app):
    with app.app_context():
        # 1. Update Task
        _update_local_task_status("t1", "started", meta="data")
        
        # 2. Check Cache
        status = _get_local_task_status("t1")
        assert status is not None
        assert status["status"] == "started"
        assert status["meta"] == "data"
        
        # 3. Direkt in Datei schreiben (simuliert anderen Prozess)
        path = app.config["TASKS_PATH"]
        with open(path, "r", encoding="utf-8") as f:
            tasks = json.load(f)
        
        tasks["t1"]["status"] = "externally_updated"
        # Zeitstempel erhöhen damit mtime sich ändert
        time.sleep(0.1) 
        with open(path, "w", encoding="utf-8") as f:
            json.dump(tasks, f)
        
        # 4. Check Cache (sollte aktualisiert werden wegen mtime-Check)
        status = _get_local_task_status("t1")
        assert status["status"] == "externally_updated"

def test_atomic_updates(app):
    with app.app_context():
        num_threads = 10
        num_updates = 5
        
        def update_worker(tid):
            for _ in range(num_updates):
                def updater(data):
                    val = data.get("counter", 0)
                    data["counter"] = val + 1
                    return data
                
                from agent.utils import update_json
                update_json(app.config["TASKS_PATH"], updater, default={})

        threads = []
        for i in range(num_threads):
            t = threading.Thread(target=update_worker, args=("shared_task",))
            threads.append(t)
            t.start()
            
        for t in threads:
            t.join()
            
        # Prüfen ob Counter korrekt ist (sollte num_threads * num_updates sein)
        with open(app.config["TASKS_PATH"], "r", encoding="utf-8") as f:
            data = json.load(f)
        
        assert data["counter"] == num_threads * num_updates
