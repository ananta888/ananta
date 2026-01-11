import os
import json
import pytest
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
    
    # I will patch the DATA_DIR in agent.ai_agent
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("agent.ai_agent.DATA_DIR", str(data_dir))
        
        # 1. Initialer Start
        app = create_app(agent="test-agent")
        initial_token = app.config.get("AGENT_TOKEN")
        
        # 2. Token rotieren
        with app.app_context():
            new_token = rotate_token()
            
        assert new_token != initial_token
        
        # 3. "Neustart" simulieren
        # Zuerst prüfen wir, ob die Datei existiert (wird sie noch nicht, da ich rotate_token noch nicht geändert habe)
        # assert token_file.exists() 
        
        app2 = create_app(agent="test-agent")
        assert app2.config.get("AGENT_TOKEN") == new_token
