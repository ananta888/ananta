import pytest
import os

# Setze Environment Variablen für Tests
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["CONTROLLER_URL"] = "http://mock-controller"
os.environ["AGENT_NAME"] = "test-agent"

from agent.ai_agent import create_app
from src.db.session import engine, SessionLocal
from src.db.session import db_session as _db_session

@pytest.fixture
def app():
    app = create_app(agent="test-agent")
    app.config.update({
        "TESTING": True,
    })
    
    # Erstelle Tabellen (falls vorhanden) in der SQLite In-Memory DB
    # Da wir momentan keine deklarativen Modelle haben, machen wir hier nichts spezielles
    # Aber wir könnten SQLAlchemy MetaData nutzen falls vorhanden
    
    yield app

@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
