import pytest
from unittest.mock import patch

def test_get_llm_history_authorized(app, client):
    """Testet den Zugriff auf /llm/history mit Authentifizierung."""
    app.config["AGENT_TOKEN"] = "test-token"
    
    # Mocking _load_lmstudio_history in agent.routes.config
    with patch('agent.routes.config._load_lmstudio_history') as mock_load:
        mock_data = {"models": {"gpt-4": {"success": 10}}}
        mock_load.return_value = mock_data
        
        response = client.get('/llm/history', headers={"Authorization": "Bearer test-token"})
        
        assert response.status_code == 200
        assert response.json == mock_data
        mock_load.assert_called_once()

def test_get_llm_history_unauthorized(app, client):
    """Testet, ob der Zugriff auf /llm/history ohne Authentifizierung verweigert wird."""
    app.config["AGENT_TOKEN"] = "test-token"
    
    response = client.get('/llm/history')
    assert response.status_code == 401
