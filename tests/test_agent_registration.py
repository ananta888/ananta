import pytest
from unittest.mock import patch, MagicMock

def test_register_agent_success(client, app):
    """Testet die erfolgreiche Registrierung eines Agenten bei erreichbarer URL."""
    with patch('agent.routes.system.http_client.get') as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response
        
        with patch('agent.routes.system.read_json') as mock_read, \
             patch('agent.routes.system.write_json') as mock_write:
            mock_read.return_value = {}
            
            payload = {
                "name": "test-agent",
                "url": "http://test-agent:5000",
                "role": "worker"
            }
            response = client.post('/register', json=payload)
            
            assert response.status_code == 200
            assert response.json['status'] == 'registered'
            mock_write.assert_called_once()

def test_register_agent_unreachable(client, app):
    """Testet die Ablehnung der Registrierung bei nicht erreichbarer URL."""
    with patch('agent.routes.system.http_client.get') as mock_get:
        # Simuliere nicht erreichbare URL
        mock_get.return_value = None
        
        payload = {
            "name": "failing-agent",
            "url": "http://invalid-url",
            "role": "worker"
        }
        response = client.post('/register', json=payload)
        
        assert response.status_code == 400
        assert "unreachable" in response.json['error'].lower()
