import pytest
import time
from agent.routes.tasks import _update_local_task_status

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
        tasks = response.json
        assert len(tasks) >= 3
        
        # 2. Filter nach Status
        response = client.get('/tasks?status=completed')
        assert response.status_code == 200
        tasks = response.json
        assert all(t["status"] == "completed" for t in tasks)
        assert any(t["id"] == "T1" for t in tasks)
        assert not any(t["id"] == "T2" for t in tasks)
        
        # 3. Filter nach Agent
        response = client.get('/tasks?agent=agent-a')
        assert response.status_code == 200
        tasks = response.json
        assert all(t.get("assigned_agent_url") == "agent-a" for t in tasks)
        assert any(t["id"] == "T1" for t in tasks)
        assert any(t["id"] == "T3" for t in tasks)
        assert not any(t["id"] == "T2" for t in tasks)
        
        # 4. Kombinierter Filter
        response = client.get('/tasks?status=todo&agent=agent-a')
        assert response.status_code == 200
        tasks = response.json
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
        tasks = response.json
        assert any(t["id"] == "T-NEW" for t in tasks)
        assert not any(t["id"] == "T-OLD" for t in tasks)
        
        # Filter: Bis vor 1 Minute
        until = now - 60
        response = client.get(f'/tasks?until={until}')
        assert response.status_code == 200
        tasks = response.json
        assert any(t["id"] == "T-OLD" for t in tasks)
        assert not any(t["id"] == "T-NEW" for t in tasks)
