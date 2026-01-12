import pytest
import time
from unittest.mock import patch, MagicMock

def test_agent_status_validation(app, client):
    """Testet die Validierung des Agenten-Status basierend auf last_seen."""
    # Auth deaktivieren für diesen Test
    app.config["AGENT_TOKEN"] = None
    
    # Pfad für die AGENTS_PATH
    agents_path = app.config["AGENTS_PATH"]
    
    # Mock Daten: Ein online Agent und ein Agent, der offline sein sollte
    now = time.time()
    mock_agents = {
        "online_agent": {
            "url": "http://online:5000",
            "last_seen": now - 10, # Vor 10 Sekunden gesehen
            "status": "online"
        },
        "offline_agent": {
            "url": "http://offline:5000",
            "last_seen": now - 600, # Vor 10 Minuten gesehen (Timeout ist 300s)
            "status": "online" # Steht noch auf online in der Datei
        }
    }
    
    with patch('agent.routes.system.read_json') as mock_read, \
         patch('agent.routes.system.write_json') as mock_write:
        
        mock_read.return_value = mock_agents.copy()
        
        # Abfrage der Agenten-Liste
        response = client.get('/agents')
        
        assert response.status_code == 200
        agents = response.json
        
        # Check online_agent
        assert agents["online_agent"]["status"] == "online"
        
        # Check offline_agent - SOLLTE jetzt offline sein
        assert agents["offline_agent"]["status"] == "offline"
        
        # Prüfen ob die Änderungen zurückgeschrieben wurden
        mock_write.assert_called_once()
        args, _ = mock_write.call_args
        assert args[0] == agents_path
        assert args[1]["offline_agent"]["status"] == "offline"
        assert args[1]["online_agent"]["status"] == "online"
