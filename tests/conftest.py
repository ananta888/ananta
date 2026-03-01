import os
from pathlib import Path

import pytest
from sqlmodel import Session, delete

# Test environment defaults
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["CONTROLLER_URL"] = "http://mock-controller"
os.environ["AGENT_NAME"] = "test-agent"
os.environ["INITIAL_ADMIN_USER"] = "admin"
os.environ["INITIAL_ADMIN_PASSWORD"] = "admin"

from agent.ai_agent import create_app
from agent.database import engine, init_db
from agent.db_models import (
    AgentInfoDB,
    ArchivedTaskDB,
    AuditLogDB,
    BannedIPDB,
    ConfigDB,
    LoginAttemptDB,
    PasswordHistoryDB,
    RefreshTokenDB,
    RoleDB,
    ScheduledTaskDB,
    StatsSnapshotDB,
    TaskDB,
    TeamDB,
    TeamMemberDB,
    TeamTypeDB,
    TeamTypeRoleLink,
    TemplateDB,
    UserDB,
)

# Initialize schema once for test process
init_db()


@pytest.fixture
def db_session():
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def cleanup_db_and_runtime(db_session):
    """Ensure every test leaves DB + runtime state clean."""
    try:
        yield
    finally:
        # FK-safe delete order
        db_session.exec(delete(TeamMemberDB))
        db_session.exec(delete(TeamTypeRoleLink))
        db_session.exec(delete(ScheduledTaskDB))
        db_session.exec(delete(ArchivedTaskDB))
        db_session.exec(delete(TaskDB))
        db_session.exec(delete(TemplateDB))
        db_session.exec(delete(TeamDB))
        db_session.exec(delete(TeamTypeDB))
        db_session.exec(delete(RoleDB))
        db_session.exec(delete(ConfigDB))
        db_session.exec(delete(AgentInfoDB))
        db_session.exec(delete(RefreshTokenDB))
        db_session.exec(delete(PasswordHistoryDB))
        db_session.exec(delete(LoginAttemptDB))
        db_session.exec(delete(BannedIPDB))
        db_session.exec(delete(StatsSnapshotDB))
        db_session.exec(delete(AuditLogDB))
        db_session.exec(delete(UserDB))
        db_session.commit()

        # Best-effort reset of long-lived in-memory components
        try:
            from agent.routes.tasks.autopilot import autonomous_loop

            autonomous_loop.stop(persist=False)
            autonomous_loop._worker_failure_streak = {}
            autonomous_loop._worker_circuit_open_until = {}
            autonomous_loop.goal = ""
            autonomous_loop.team_id = ""
            autonomous_loop.budget_label = ""
            autonomous_loop.last_error = None
        except Exception:
            pass

        try:
            from agent.routes.tasks.auto_planner import auto_planner

            auto_planner.enabled = False
            auto_planner.auto_followup_enabled = True
        except Exception:
            pass

        # Best-effort filesystem cleanup for legacy test artifacts
        for rel in (
            'data_test/users.json',
            'data_test/refresh_tokens.json',
            'data_test/llm_model_history.json',
            'data_test/llm_model_benchmarks.json',
        ):
            try:
                Path(rel).unlink(missing_ok=True)
            except Exception:
                pass


@pytest.fixture
def app():
    app = create_app(agent="test-agent")
    app.config.update({"TESTING": True})
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
        from agent.auth import hash_password
        from agent.repository import user_repo

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
