import pytest
from unittest.mock import patch, MagicMock
import json

def test_task_specific_endpoints_path(client, app):
    """Verifiziert, dass die neuen Task-spezifischen Endpunkte erreichbar sind."""
    
    tid = "T-123456"
    
    # Wir müssen sicherstellen, dass der Task in der lokalen "Datenbank" existiert
    # Da wir in Tests oft In-Memory oder Mock-Pfade nutzen, schauen wir uns _update_local_task_status an.
    with app.app_context():
        from agent.routes.tasks import _update_local_task_status
        _update_local_task_status(tid, "assigned", assigned_to="test-agent")

    # 1. Propose auf dem neuen Pfad
    with patch('agent.routes.tasks._call_llm') as mock_llm:
        mock_llm.return_value = '{"reason": "Test", "command": "echo hello"}'
        response = client.post(f'/tasks/{tid}/step/propose', json={"prompt": "test"})
        assert response.status_code == 200
        assert response.json["command"] == "echo hello"

    # 2. Execute auf dem neuen Pfad
    with patch('agent.shell.PersistentShell.execute') as mock_exec:
        mock_exec.return_value = ("hello", 0)
        # Wir müssen ein last_proposal im Task haben, damit execute funktioniert
        with app.app_context():
            _update_local_task_status(tid, "proposing", last_proposal={"command": "echo hello", "reason": "Test"})
            
        response = client.post(f'/tasks/{tid}/step/execute', json={})
        assert response.status_code == 200
        assert response.json["output"] == "hello"

def test_task_specific_endpoints_old_path_fail(client):
    """Verifiziert, dass die alten Pfade nicht mehr funktionieren (404)."""
    tid = "T-123456"
    
    response = client.post(f'/tasks/{tid}/propose', json={})
    assert response.status_code == 404
    
    response = client.post(f'/tasks/{tid}/execute', json={})
    assert response.status_code == 404
