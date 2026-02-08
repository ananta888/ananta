import pytest
import os

# Setze Environment Variablen für Tests
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["CONTROLLER_URL"] = "http://mock-controller"
os.environ["AGENT_NAME"] = "test-agent"
os.environ["INITIAL_ADMIN_USER"] = "admin"
os.environ["INITIAL_ADMIN_PASSWORD"] = "admin"

from agent.ai_agent import create_app

@pytest.fixture
def app():
    app = create_app(agent="test-agent")
    app.config.update({
        "TESTING": True,
    })
    
    yield app

@pytest.fixture
def client(app):
    return app.test_client()
