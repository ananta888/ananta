import pytest
from unittest.mock import patch, MagicMock

def test_health_endpoint(client):
    """Testet den einfachen Health-Endpunkt."""
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json['status'] == 'success'
    assert 'data' in response.json
    assert response.json['data']['checks'] is not None

def test_ready_endpoint_success(client):
    """Testet den Readiness-Endpunkt bei Erfolg."""
    # Wir müssen den Hub-Check und LLM-Check mocken, da diese HTTP-Requests machen
    with patch('agent.common.http.HttpClient.get') as mock_get:
        # Mock für Hub und LLM
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response
        
        response = client.get('/ready')
            
    assert response.status_code == 200
    data = response.json['data']
    assert data['ready'] is True
    assert 'hub' in data['checks']
    assert data['checks']['hub']['status'] == 'ok'

def test_ready_endpoint_failure(client):
    """Testet den Readiness-Endpunkt bei Fehler."""
    with patch('agent.common.http.HttpClient.get') as mock_get:
        # Mock liefert None (Fehler)
        mock_get.return_value = None
        
        response = client.get('/ready')
            
    assert response.status_code == 503
    data = response.json['data']
    assert data['ready'] is False
    assert data['checks']['hub']['status'] == 'error'

def test_auth_required_when_token_set(app, client):
    """Testet, ob Authentifizierung erzwungen wird, wenn ein Token gesetzt ist."""
    app.config["AGENT_TOKEN"] = "secret-token"
    
    # Ohne Header
    response = client.get('/config')
    assert response.status_code == 401
    assert response.json['message'] == 'unauthorized'
    assert response.json['data']['details'] == 'Missing Authorization (header or token param)'
    
    # Mit falschem Header
    response = client.get('/config', headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401
    
    # Mit korrektem Header (statischer Token)
    response = client.get('/config', headers={"Authorization": "Bearer secret-token"})
    assert response.status_code == 200

def test_auth_with_jwt(app, client):
    """Testet die JWT-Authentifizierung."""
    import jwt
    import time
    secret = "secret-token"
    app.config["AGENT_TOKEN"] = secret
    
    payload = {"user": "admin", "exp": time.time() + 3600}
    token = jwt.encode(payload, secret, algorithm="HS256")
    
    response = client.get('/config', headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

def test_list_tasks_unauthorized(app, client):
    """Testet, ob der Zugriff auf /tasks geschützt ist."""
    app.config["AGENT_TOKEN"] = "secret-token"
    response = client.get('/tasks')
    assert response.status_code == 401

def test_list_tasks_authorized(app, client):
    """Testet den Zugriff auf /tasks mit Authentifizierung."""
    app.config["AGENT_TOKEN"] = "secret-token"
    # Mocking task_repo to avoid database access
    with patch('agent.routes.tasks.management.task_repo') as mock_repo:
        mock_repo.get_paged.return_value = []
        response = client.get('/tasks', headers={"Authorization": "Bearer secret-token"})
        assert response.status_code == 200
        assert response.json == []
