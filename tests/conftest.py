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
from agent.db_models import TaskDB, TemplateDB, TeamDB, RoleDB, UserDB, RefreshTokenDB, ConfigDB, AgentInfoDB

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
    db_session.exec(delete(AgentInfoDB))
    db_session.exec(delete(UserDB))
    db_session.exec(delete(RefreshTokenDB))
    # UserDB und RefreshTokenDB werden oft in spezifischen Tests verwaltet,
    # aber wir können sie hier auch optional bereinigen oder in den Tests selbst.
    db_session.commit()


@pytest.fixture
def app():
    app = create_app(agent="test-agent")
    app.config.update(
        {
            "TESTING": True,
        }
    )

    yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_header(client):
    """Returns a valid auth header for a regular user."""
    response = client.post("/login", json={"username": "admin", "password": "admin"})
    token = response.json["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def user_auth_header(client, app):
    """Creates a regular user and returns auth header."""
    with app.app_context():
        from agent.repository import user_repo
        from werkzeug.security import generate_password_hash

        user_repo.save(
            user_repo.UserDB(username="testuser", password_hash=generate_password_hash("testpass"), role="user")
        )

    response = client.post("/login", json={"username": "testuser", "password": "testpass"})
    token = response.json["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_auth_header(client):
    """Returns a valid auth header for an admin user."""
    response = client.post("/login", json={"username": "admin", "password": "admin"})
    token = response.json["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def user_auth_header(client, app):
    """Creates a regular user and returns auth header."""
    with app.app_context():
        from agent.repository import user_repo
        from agent.auth import hash_password

        user_repo.create("testuser", hash_password("testpass"), role="user")

    response = client.post("/login", json={"username": "testuser", "password": "testpass"})
    token = response.json["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_auth_header(client):
    """Returns a valid auth header for an admin user."""
    response = client.post("/login", json={"username": "admin", "password": "admin"})
    token = response.json["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}
