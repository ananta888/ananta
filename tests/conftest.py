import os
from pathlib import Path
from typing import Any

import pytest

# Test environment defaults
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["CONTROLLER_URL"] = "http://mock-controller"
os.environ["AGENT_NAME"] = "test-agent"
os.environ["INITIAL_ADMIN_USER"] = "admin"
os.environ["INITIAL_ADMIN_PASSWORD"] = "admin"

_TEST_DB_READY = False


def _settings():
    from agent.config import settings

    return settings


def _ensure_test_db() -> None:
    global _TEST_DB_READY
    if _TEST_DB_READY:
        return
    from agent.database import init_db

    init_db()
    _TEST_DB_READY = True


def _db_engine():
    _ensure_test_db()
    from agent.database import engine

    return engine


def _db_runtime() -> dict[str, Any]:
    _ensure_test_db()
    from sqlalchemy import inspect
    from sqlalchemy.exc import OperationalError
    from sqlmodel import Session, delete

    from agent.db_models import (
        ActionPackDB,
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
        InstructionOverlayDB,
        ExtractedDocumentDB,
        EvolutionProposalDB,
        EvolutionRunDB,
        GoalDB,
        KnowledgeCollectionDB,
        KnowledgeIndexDB,
        KnowledgeIndexRunDB,
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
        UserInstructionProfileDB,
        UserDB,
        VerificationRecordDB,
        WorkerJobDB,
        WorkerResultDB,
    )

    return {
        "engine": _db_engine(),
        "inspect": inspect,
        "OperationalError": OperationalError,
        "Session": Session,
        "delete": delete,
        "models": (
            ActionPackDB,
            WorkerResultDB,
            WorkerJobDB,
            EvolutionProposalDB,
            EvolutionRunDB,
            ContextBundleDB,
            InstructionOverlayDB,
            RetrievalRunDB,
            MemoryEntryDB,
            KnowledgeIndexRunDB,
            KnowledgeIndexDB,
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
            UserInstructionProfileDB,
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
        ),
    }


@pytest.fixture
def db_session():
    runtime = _db_runtime()
    with runtime["Session"](runtime["engine"]) as session:
        yield session


@pytest.fixture(autouse=True)
def cleanup_db_and_runtime():
    """Ensure every test leaves DB + runtime state clean."""
    def _reset_runtime_state():
        try:
            _settings().shell_path = "sh"
        except Exception:
            pass

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

        try:
            from agent.shell import _close_global_shells

            _close_global_shells()
        except Exception:
            pass

        try:
            from agent.services.background.registration import reset_registration_state

            reset_registration_state()
        except Exception:
            pass

        runtime = _db_runtime()
        inspector = runtime["inspect"](runtime["engine"])
        session_cls = runtime["Session"]
        delete_stmt = runtime["delete"]
        operational_error = runtime["OperationalError"]

        def _delete_if_table_exists(model):
            try:
                if inspector.has_table(model.__tablename__):
                    with session_cls(runtime["engine"]) as session:
                        session.exec(delete_stmt(model))
                        session.commit()
            except operational_error:
                pass

        for model in runtime["models"]:
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
        data_dir = Path(_settings().data_dir)
        for rel_dir in ("artifacts", "knowledge_indices"):
            target_dir = data_dir / rel_dir
            if target_dir.exists():
                for path in sorted(target_dir.rglob("*"), reverse=True):
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
    _ensure_test_db()
    from agent.ai_agent import create_app

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
        from werkzeug.security import generate_password_hash
        from agent.db_models import UserDB
        from agent.repository import user_repo

        user_repo.save(UserDB(username="testuser", password_hash=generate_password_hash("testpass"), role="user"))

    response = client.post("/login", json={"username": "testuser", "password": "testpass"})
    token = response.json["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_auth_header(client):
    """Returns a valid auth header for an admin user."""
    response = client.post("/login", json={"username": "admin", "password": "admin"})
    token = response.json["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}
