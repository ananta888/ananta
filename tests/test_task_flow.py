import pytest
from unittest.mock import patch, MagicMock

def test_task_propose_and_execute(client):
    """Simuliert einen Task-Flow: Propose und Execute."""
    
    # 1. Propose Step
    propose_data = {
        "task_id": "task-123",
        "step_id": 1,
        "prompt": "Berechne 2+2",
        "context": {}
    }
    
    # Wir müssen den LLM-Call mocken
    with patch('agent.ai_agent._call_llm') as mock_llm:
        mock_llm.return_value = "Ich schlage vor, den Befehl 'echo 4' auszuführen. REASON: Einfache Berechnung."
        
        response = client.post('/step/propose', json=propose_data)
        
    assert response.status_code == 200
    assert "command" in response.json
    assert "echo 4" in response.json["command"]
    
    # 2. Execute Step
    execute_data = {
        "task_id": "task-123",
        "step_id": 1,
        "command": "echo 4"
    }
    
    # Wir müssen die Command-Execution mocken (wir wollen ja nichts echtes ausführen)
    with patch('agent.ai_agent._execute_command') as mock_exec:
        mock_exec.return_value = ("4", 0)
        
        response = client.post('/step/execute', json=execute_data)
        
    assert response.status_code == 200
    assert response.json["exit_code"] == 0
    assert response.json["stdout"] == "4"
