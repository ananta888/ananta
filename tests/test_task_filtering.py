import pytest
import time
from agent.routes.tasks.utils import _update_local_task_status

def test_list_tasks_filtering(client, app):
    """Testet die Filterung von Tasks über die API."""
    
    with app.app_context():
        # Testdaten anlegen
        _update_local_task_status("T1", "completed", assigned_agent_url="agent-a")
        _update_local_task_status("T2", "failed", assigned_agent_url="agent-b")
        _update_local_task_status("T3", "todo", assigned_agent_url="agent-a")
        
        # 1. Kein Filter
        response = client.get('/tasks')
        assert response.status_code == 200
        tasks = response.json["data"]
        assert len(tasks) >= 3
        
        # 2. Filter nach Status
        response = client.get('/tasks?status=completed')
        assert response.status_code == 200
        tasks = response.json["data"]
        assert all(t["status"] == "completed" for t in tasks)
        assert any(t["id"] == "T1" for t in tasks)
        assert not any(t["id"] == "T2" for t in tasks)
        
        # 3. Filter nach Agent
        response = client.get('/tasks?agent=agent-a')
        assert response.status_code == 200
        tasks = response.json["data"]
        assert all(t.get("assigned_agent_url") == "agent-a" for t in tasks)
        assert any(t["id"] == "T1" for t in tasks)
        assert any(t["id"] == "T3" for t in tasks)
        assert not any(t["id"] == "T2" for t in tasks)
        
        # 4. Kombinierter Filter
        response = client.get('/tasks?status=todo&agent=agent-a')
        assert response.status_code == 200
        tasks = response.json["data"]
        assert len(tasks) == 1
        assert tasks[0]["id"] == "T3"

def test_list_tasks_time_filtering(client, app):
    """Testet die Zeitfilterung von Tasks."""
    
    with app.app_context():
        # Wir faken die Zeiten, indem wir direkt aktualisieren
        now = time.time()
        _update_local_task_status("T-OLD", "todo", created_at=now - 10000)
        _update_local_task_status("T-NEW", "todo", created_at=now - 10)
        
        # Filter: Seit vor 1 Minute
        since = now - 60
        response = client.get(f'/tasks?since={since}')
        assert response.status_code == 200
        tasks = response.json["data"]
        assert any(t["id"] == "T-NEW" for t in tasks)
        assert not any(t["id"] == "T-OLD" for t in tasks)
        
        # Filter: Bis vor 1 Minute
        until = now - 60
        response = client.get(f'/tasks?until={until}')
        assert response.status_code == 200
        tasks = response.json["data"]
        assert any(t["id"] == "T-OLD" for t in tasks)
        assert not any(t["id"] == "T-NEW" for t in tasks)


def test_list_tasks_status_alias_filtering(client, app):
    with app.app_context():
        _update_local_task_status("T-ALIAS-DONE", "done")
        _update_local_task_status("T-ALIAS-IP", "in-progress")
        _update_local_task_status("T-ALIAS-TODO", "to-do")

        res_done = client.get("/tasks?status=done")
        assert res_done.status_code == 200
        assert any(t["id"] == "T-ALIAS-DONE" and t["status"] == "completed" for t in res_done.json["data"])

        res_ip = client.get("/tasks?status=in-progress")
        assert res_ip.status_code == 200
        assert any(t["id"] == "T-ALIAS-IP" and t["status"] == "in_progress" for t in res_ip.json["data"])

        res_todo = client.get("/tasks?status=to-do")
        assert res_todo.status_code == 200
        assert any(t["id"] == "T-ALIAS-TODO" and t["status"] == "todo" for t in res_todo.json["data"])


def test_list_tasks_status_filter_uses_db_query_path(client, app, monkeypatch):
    with app.app_context():
        _update_local_task_status("T-DBQ-1", "done")

    def _fail_get_all():
        raise AssertionError("get_all should not be used for status-filtered list")

    monkeypatch.setattr("agent.routes.tasks.management.task_repo.get_all", _fail_get_all)
    res = client.get("/tasks?status=completed")
    assert res.status_code == 200
    assert any(t["id"] == "T-DBQ-1" for t in res.json["data"])
