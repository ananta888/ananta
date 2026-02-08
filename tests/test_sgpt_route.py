import pytest
from unittest.mock import patch, MagicMock
import subprocess

def test_sgpt_execute_success(client):
    """Testet den SGPT Execute Proxy Endpunkt bei Erfolg."""
    with patch('subprocess.run') as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ls -la\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result
        
        payload = {
            "prompt": "list files",
            "options": ["--shell"]
        }
        
        response = client.post('/api/sgpt/execute', json=payload)
        
        assert response.status_code == 200
        assert response.json['status'] == 'success'
        assert "ls -la" in response.json['data']['output']
        mock_run.assert_called_once()

def test_sgpt_execute_missing_prompt(client):
    """Testet den SGPT Execute Proxy Endpunkt mit fehlendem Prompt."""
    payload = {
        "options": ["--shell"]
    }
    response = client.post('/api/sgpt/execute', json=payload)
    assert response.status_code == 400
    assert "Missing prompt" in response.json['message']
    assert response.json['status'] == 'error'

def test_sgpt_execute_error(client):
    """Testet den SGPT Execute Proxy Endpunkt bei einer Exception."""
    with patch('subprocess.run') as mock_run:
        mock_run.side_effect = Exception("Internal Error")
        
        payload = {
            "prompt": "list files"
        }
        
        response = client.post('/api/sgpt/execute', json=payload)
        
        assert response.status_code == 500
        assert "Internal Error" in response.json['message']
        assert response.json['status'] == 'error'
