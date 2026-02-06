import pytest
from unittest.mock import patch, MagicMock

def test_sgpt_execute_success(client):
    """Testet den SGPT Execute Proxy Endpunkt bei Erfolg."""
    # Mocking get_sgpt_main to avoid actual import and execution
    with patch('agent.routes.sgpt.get_sgpt_main') as mock_get_main:
        mock_sgpt = MagicMock()
        mock_get_main.return_value = mock_sgpt
        
        # Wir simulieren eine Ausgabe in stdout
        def side_effect():
            import sys
            sys.stdout.write("ls -la\n")
            
        mock_sgpt.side_effect = side_effect
        
        payload = {
            "prompt": "list files",
            "options": ["--shell"]
        }
        
        response = client.post('/api/sgpt/execute', json=payload)
        
        assert response.status_code == 200
        assert response.json['status'] == 'success'
        assert "ls -la" in response.json['output']
        mock_sgpt.assert_called_once()

def test_sgpt_execute_missing_prompt(client):
    """Testet den SGPT Execute Proxy Endpunkt mit fehlendem Prompt."""
    payload = {
        "options": ["--shell"]
    }
    response = client.post('/api/sgpt/execute', json=payload)
    assert response.status_code == 400
    assert "Missing prompt" in response.json['error']

def test_sgpt_execute_error(client):
    """Testet den SGPT Execute Proxy Endpunkt bei einer Exception."""
    with patch('agent.routes.sgpt.get_sgpt_main') as mock_get_main:
        mock_sgpt = MagicMock()
        mock_get_main.return_value = mock_sgpt
        mock_sgpt.side_effect = Exception("Internal Error")
        
        payload = {
            "prompt": "list files"
        }
        
        response = client.post('/api/sgpt/execute', json=payload)
        
        assert response.status_code == 500
        assert "Internal Error" in response.json['error']
        assert response.json['status'] == 'error'
