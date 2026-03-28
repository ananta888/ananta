import os
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError
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
    ArtifactDB,
    ArtifactVersionDB,
    ArchivedTaskDB,
    AuditLogDB,
    BannedIPDB,
    BlueprintArtifactDB,
    BlueprintRoleDB,
    ConfigDB,
    ContextBundleDB,
    ExtractedDocumentDB,
    GoalDB,
    KnowledgeCollectionDB,
    KnowledgeLinkDB,
    LoginAttemptDB,
    MemoryEntryDB,
    PasswordHistoryDB,
    PlanDB,
    PlanNodeDB,
    PolicyDecisionDB,
    RefreshTokenDB,
    RetrievalRunDB,
    RoleDB,
    ScheduledTaskDB,
    StatsSnapshotDB,
    TaskDB,
    TeamDB,
    TeamBlueprintDB,
    TeamMemberDB,
    TeamTypeDB,
    TeamTypeRoleLink,
    TemplateDB,
    UserDB,
    VerificationRecordDB,
    WorkerJobDB,
    WorkerResultDB,
)

# Initialize schema once for test process
init_db()


@pytest.fixture
def db_session():
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def cleanup_db_and_runtime():
    """Ensure every test leaves DB + runtime state clean."""
    def _reset_runtime_state():
        try:
            from agent.routes.tasks.autopilot import autonomous_loop

            autonomous_loop.stop(persist=False)
            autonomous_loop.running = False
            autonomous_loop.interval_seconds = 20
            autonomous_loop.max_concurrency = 2
            autonomous_loop.last_tick_at = None
            autonomous_loop._worker_failure_streak = {}
            autonomous_loop._worker_circuit_open_until = {}
            autonomous_loop._worker_cursor = 0
            autonomous_loop.started_at = None
            autonomous_loop.tick_count = 0
            autonomous_loop.dispatched_count = 0
            autonomous_loop.completed_count = 0
            autonomous_loop.failed_count = 0
            autonomous_loop.goal = ""
            autonomous_loop.team_id = ""
            autonomous_loop.budget_label = ""
            autonomous_loop.security_level = "safe"
            autonomous_loop.last_error = None
            autonomous_loop._app = None
        except Exception:
            pass

        inspector = inspect(engine)

        def _delete_if_table_exists(model):
            try:
                if inspector.has_table(model.__tablename__):
                    with Session(engine) as session:
                        session.exec(delete(model))
                        session.commit()
            except OperationalError:
                pass

        for model in (
            WorkerResultDB,
            WorkerJobDB,
            ContextBundleDB,
            RetrievalRunDB,
            MemoryEntryDB,
            KnowledgeLinkDB,
            ExtractedDocumentDB,
            ArtifactVersionDB,
            ArtifactDB,
            KnowledgeCollectionDB,
            TeamMemberDB,
            BlueprintArtifactDB,
            BlueprintRoleDB,
            TeamTypeRoleLink,
            ScheduledTaskDB,
            ArchivedTaskDB,
            TaskDB,
            PlanNodeDB,
            PlanDB,
            GoalDB,
            TemplateDB,
            TeamDB,
            TeamBlueprintDB,
            TeamTypeDB,
            RoleDB,
            ConfigDB,
            AgentInfoDB,
            RefreshTokenDB,
            PasswordHistoryDB,
            LoginAttemptDB,
            BannedIPDB,
            StatsSnapshotDB,
            PolicyDecisionDB,
            VerificationRecordDB,
            AuditLogDB,
            UserDB,
        ):
            _delete_if_table_exists(model)
        try:
            from agent.routes.tasks.auto_planner import auto_planner

            auto_planner.enabled = False
            auto_planner.auto_followup_enabled = True
            auto_planner.auto_start_autopilot = False
            auto_planner.max_subtasks_per_goal = 10
            auto_planner.default_priority = "Medium"
            auto_planner.llm_timeout = 30
            auto_planner.llm_retry_attempts = 2
            auto_planner.llm_retry_backoff = 0.5
            auto_planner._stats = {
                "goals_processed": 0,
                "tasks_created": 0,
                "followups_created": 0,
                "errors": 0,
                "llm_retries": 0,
            }
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
        artifacts_dir = Path("data_test/artifacts")
        if artifacts_dir.exists():
            for path in sorted(artifacts_dir.rglob("*"), reverse=True):
                try:
                    if path.is_file():
                        path.unlink(missing_ok=True)
                    elif path.is_dir():
                        path.rmdir()
                except Exception:
                    pass

    _reset_runtime_state()
    try:
        yield
    finally:
        _reset_runtime_state()


@pytest.fixture
def app():
    app = create_app(agent="test-agent")
    app.config.update(
        {
            "TESTING": True,
            "AGENT_TOKEN": "test-agent-token-with-sufficient-length-1234567890",
        }
    )
    try:
        from agent.routes.tasks.auto_planner import auto_planner
        from agent.routes.tasks.autopilot import autonomous_loop

        auto_planner.auto_start_autopilot = False
        autonomous_loop.bind_app(app)
    except Exception:
        pass
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
