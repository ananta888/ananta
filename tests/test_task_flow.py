import pytest
from unittest.mock import patch, MagicMock

def test_task_propose_and_execute(client):
    """Simuliert einen Task-Flow: Propose und Execute."""
    
    # 1. Propose Step
    propose_data = {
        "task_id": "task-123",
        "prompt": "Berechne 2+2"
    }
    
    # Wir müssen den LLM-Call mocken
    with patch('agent.routes.tasks.execution._call_llm') as mock_llm:
        mock_llm.return_value = '{"reason": "Einfache Berechnung.", "command": "echo 4"}'
        
        response = client.post('/step/propose', json=propose_data)
        
    assert response.status_code == 200
    assert "command" in response.json["data"]
    assert "echo 4" in response.json["data"]["command"]
    
    # 2. Execute Step
    execute_data = {
        "task_id": "task-123",
        "command": "echo 4"
    }
    
    # Wir müssen die Shell-Execution mocken
    with patch('agent.shell.PersistentShell.execute') as mock_exec:
        mock_exec.return_value = ("4", 0)
        
        # Auth wird hier übersprungen, da AGENT_TOKEN in der Config leer sein könnte (Standard in Tests)
        # Falls Auth aktiv ist, müssten wir einen Header mitschicken.
        response = client.post('/step/execute', json=execute_data)
        
    assert response.status_code == 200
    assert response.json["data"]["exit_code"] == 0
    assert response.json["data"]["output"] == "4"
