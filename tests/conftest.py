import pytest
import os

# Setze Environment Variablen für Tests
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["CONTROLLER_URL"] = "http://mock-controller"
os.environ["AGENT_NAME"] = "test-agent"
os.environ["INITIAL_ADMIN_USER"] = "admin"
os.environ["INITIAL_ADMIN_PASSWORD"] = "admin"

from agent.ai_agent import create_app
from agent.database import engine, init_db
from sqlmodel import Session, delete
from agent.db_models import TaskDB, TemplateDB, TeamDB, RoleDB, UserDB, RefreshTokenDB, ConfigDB

# Initialisiere DB-Schema für Tests
init_db()

@pytest.fixture
def db_session():
    with Session(engine) as session:
        yield session

@pytest.fixture(autouse=True)
def cleanup_db(db_session):
    """Löscht Test-Daten nach jedem Test."""
    yield
    # Cleanup nach dem Test
    db_session.exec(delete(TaskDB))
    db_session.exec(delete(TemplateDB))
    db_session.exec(delete(TeamDB))
    db_session.exec(delete(RoleDB))
    db_session.exec(delete(ConfigDB))
    db_session.exec(delete(UserDB))
    db_session.exec(delete(RefreshTokenDB))
    # UserDB und RefreshTokenDB werden oft in spezifischen Tests verwaltet, 
    # aber wir können sie hier auch optional bereinigen oder in den Tests selbst.
    db_session.commit()

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
