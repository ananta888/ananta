import pytest
from unittest.mock import patch, MagicMock

def test_health_endpoint(client):
    """Testet den einfachen Health-Endpunkt."""
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json['status'] == 'ok'

def test_ready_endpoint_success(client):
    """Testet den Readiness-Endpunkt bei Erfolg."""
    # Wir müssen den Controller-Check und LLM-Check mocken, da diese HTTP-Requests machen
    with patch('src.common.http.HttpClient.get') as mock_get:
        # Mock für Controller und LLM
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response
        
        # Mock für DB Health Check
        with patch('agent.health.check_db_health', return_value=True):
            response = client.get('/ready')
            
    assert response.status_code == 200
    assert response.json['ready'] is True
    assert 'database' in response.json['checks']
    assert response.json['checks']['database']['status'] == 'ok'

def test_ready_endpoint_db_failure(client):
    """Testet den Readiness-Endpunkt bei DB-Fehler."""
    with patch('src.common.http.HttpClient.get') as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response
        
        with patch('agent.health.check_db_health', return_value=False):
            response = client.get('/ready')
            
    assert response.status_code == 503
    assert response.json['ready'] is False
    assert response.json['checks']['database']['status'] == 'error'
