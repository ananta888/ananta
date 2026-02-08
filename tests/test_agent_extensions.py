import pytest
from flask import Blueprint, jsonify
from agent.ai_agent import create_app
from agent.config import settings

# Ein Mock-Blueprint für die Extension
mock_bp = Blueprint("mock_extension", __name__)

@mock_bp.route("/mock-ext-route")
def mock_route():
    return jsonify({"status": "extension_ok"})

def test_agent_extensions_loading(monkeypatch):
    """
    Testet, ob externe Blueprints über die AGENT_EXTENSIONS Konfiguration korrekt geladen werden.
    """
    # Wir müssen das Modul mockable machen oder so tun, als gäbe es ein Modul
    # Da wir in Python sind, können wir ein temporäres Modul simulieren.
    
    class MockModule:
        bp = mock_bp
        
    # Wir monkeypatchen __import__, um unser Mock-Modul zurückzugeben
    original_import = __import__
    def mocked_import(name, *args, **kwargs):
        if name == "my_test_extension":
            return MockModule
        return original_import(name, *args, **kwargs)
    
    monkeypatch.setattr("builtins.__import__", mocked_import)
    
    # Konfiguration für die Extension setzen
    monkeypatch.setattr(settings, "extensions", "my_test_extension")
    
    # App erstellen (lädt Extensions in _load_extensions)
    app = create_app(agent="test-agent")
    app.config["TESTING"] = True
    client = app.test_client()
    
    # Prüfen, ob die Route der Extension erreichbar ist
    response = client.get("/mock-ext-route")
    assert response.status_code == 200
    assert response.get_json() == {"status": "extension_ok"}

def test_agent_extensions_init_app(monkeypatch):
    """
    Testet, ob Extensions mit init_app korrekt geladen werden.
    """
    initialized = False
    
    class MockModuleInit:
        @staticmethod
        def init_app(app):
            nonlocal initialized
            initialized = True
            @app.route("/init-app-route")
            def init_route():
                return jsonify({"status": "init_app_ok"})
    
    original_import = __import__
    def mocked_import(name, *args, **kwargs):
        if name == "my_init_extension":
            return MockModuleInit
        return original_import(name, *args, **kwargs)
    
    monkeypatch.setattr("builtins.__import__", mocked_import)
    monkeypatch.setattr(settings, "extensions", "my_init_extension")
    
    app = create_app(agent="test-agent")
    app.config["TESTING"] = True
    client = app.test_client()
    
    assert initialized is True
    response = client.get("/init-app-route")
    assert response.status_code == 200
    assert response.get_json() == {"status": "init_app_ok"}
