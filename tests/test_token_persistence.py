import os
import json
import pytest
from unittest.mock import patch
from agent.ai_agent import create_app
from agent.auth import rotate_token

def test_token_persistence(tmp_path):
    # Setup: Nutze ein temporäres Verzeichnis für DATA_DIR
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    token_file = data_dir / "token.json"
    
    # Mocking DATA_DIR in ai_agent is tricky because it's a module level constant
    # But we can override app.config after creation, 
    # however the loading happens DURING create_app.
    
    # I will patch the data_dir in settings
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("agent.ai_agent.settings.data_dir", str(data_dir))
        mp.setattr("agent.auth.settings.data_dir", str(data_dir))
        # Mock register_with_hub to avoid real HTTP requests and timeouts
        mp.setattr("agent.auth.register_with_hub", lambda **kwargs: True)
        mp.setattr("agent.ai_agent.register_with_hub", lambda **kwargs: True)
        
        # 1. Initialer Start
        app = create_app(agent="test-agent")
        initial_token = app.config.get("AGENT_TOKEN")
        
        # 2. Token rotieren
        with app.app_context():
            new_token = rotate_token()
            
        assert new_token != initial_token
        
        # 3. Berechtigungen prüfen
        assert token_file.exists()
        if os.name != 'nt':
             # Auf Unix-Systemen Berechtigungen prüfen (0600)
             mode = os.stat(token_file).st_mode & 0o777
             assert mode == 0o600
        
        # 4. "Neustart" simulieren
        app2 = create_app(agent="test-agent")
        assert app2.config.get("AGENT_TOKEN") == new_token

def test_token_persistence_from_hub(tmp_path):
    """Testet, ob der vom Hub zurückgegebene Token persistiert wird."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    token_file = data_dir / "token.json"
    
    from agent.utils import register_with_hub
    import agent.config
    
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("agent.config.settings.data_dir", str(data_dir))
        
        # Mock _http_post um einen Token zurückzugeben
        with patch("agent.utils._http_post") as mock_post:
            mock_post.return_value = {"status": "ok", "agent_token": "hub-generated-token"}
            
            success = register_with_hub(
                hub_url="http://hub",
                agent_name="test-agent",
                port=5000,
                token="old-token"
            )
            
            assert success is True
            assert token_file.exists()
            with open(token_file, "r") as f:
                data = json.load(f)
                assert data["agent_token"] == "hub-generated-token"
