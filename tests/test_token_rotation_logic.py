import time
import pytest
import json
from unittest.mock import MagicMock, patch
from agent.ai_agent import _check_token_rotation

def test_check_token_rotation_triggers(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    token_file = data_dir / "token.json"
    
    # Token Datei mit altem Datum erstellen
    old_time = time.time() - (8 * 86400) # 8 Tage her (Default ist 7)
    token_data = {"agent_token": "old-token", "last_rotation": old_time}
    with open(token_file, "w") as f:
        json.dump(token_data, f)
        
    app = MagicMock()
    app.config = {"TOKEN_PATH": str(token_file)}
    
    with patch("agent.auth.rotate_token") as mock_rotate:
        with patch("agent.ai_agent.settings") as mock_settings:
            mock_settings.token_rotation_days = 7
            # Mocking read_json because it's used in _check_token_rotation
            with patch("agent.ai_agent.read_json", return_value=token_data):
                _check_token_rotation(app)
                assert mock_rotate.called

def test_check_token_rotation_not_triggers_early(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    token_file = data_dir / "token.json"
    
    # Token Datei mit neuem Datum erstellen
    new_time = time.time() - (1 * 86400) # 1 Tag her
    token_data = {"agent_token": "old-token", "last_rotation": new_time}
    with open(token_file, "w") as f:
        json.dump(token_data, f)
        
    app = MagicMock()
    app.config = {"TOKEN_PATH": str(token_file)}
    
    with patch("agent.auth.rotate_token") as mock_rotate:
        with patch("agent.ai_agent.settings") as mock_settings:
            mock_settings.token_rotation_days = 7
            with patch("agent.ai_agent.read_json", return_value=token_data):
                _check_token_rotation(app)
                assert not mock_rotate.called
