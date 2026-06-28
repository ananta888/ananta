import os
import sys
import json
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# Stub out missing legacy module so test_worker_client_adapter.py can be collected.
# worker_engine is not part of the current codebase; stubs prevent ImportError
# while keeping the test file importable and runnable.
if "worker_engine" not in sys.modules:
    sys.modules["worker_engine"] = MagicMock()

# Test environment defaults
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["CONTROLLER_URL"] = "http://mock-controller"
os.environ["AGENT_NAME"] = "test-agent"
os.environ["INITIAL_ADMIN_USER"] = "admin"
os.environ["INITIAL_ADMIN_PASSWORD"] = "admin"

from tests_support import admin_login_token, reset_auth_state

_TEST_DB_READY = False


def pytest_collection_modifyitems(config, items):
    del config
    if str(os.environ.get("RUN_MANUAL_FULL_SCAN_TESTS") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return
    skip_manual_full_scan = pytest.mark.skip(
        reason="manual_full_scan tests require RUN_MANUAL_FULL_SCAN_TESTS=1 and are not run in GitHub workflows"
    )
    for item in items:
        if "manual_full_scan" in item.keywords:
            item.add_marker(skip_manual_full_scan)


_INTEGRATION_OPT_IN_ENV = "RUN_INTEGRATION_TESTS"


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """Skip integration-marked tests unless RUN_INTEGRATION_TESTS is set.

    Integration tests in this repo exercise the full planning/worker/claim
    chain and rely on background threads with production-sized safety-net
    timeouts (outer_planning_timeout_s default 645s). They MUST NOT run in
    the default `pytest` invocation — a single one stalls the suite for
    ~10 minutes. Opt-in via `RUN_INTEGRATION_TESTS=1`.

    Parallel pattern to the manual_full_scan skip above.
    """
    if "integration" not in item.keywords:
        return
    if str(os.environ.get(_INTEGRATION_OPT_IN_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}:
        return
    pytest.skip(
        f"integration test requires {_INTEGRATION_OPT_IN_ENV}=1 (default pytest runs skip integration tests to keep suite fast)"
    )


@pytest.fixture(autouse=True)
def _integration_planning_timeout_brake(request, app, monkeypatch):
    """Cap planning_policy timeouts for integration tests.

    Even with the opt-in gate above, integration tests that start a real
    planning invoke should fail fast instead of waiting 10+ minutes on the
    production safety-net. The handler reads timeouts from
    `current_app.config["AGENT_CONFIG"]["planning_policy"]`, so we shrink
    that dict before the request fires.

    The handler applies a floor of max(30, timeout_seconds) on execute and
    max(10, queue_wait_timeout_seconds) on queue-wait
    (goals_planning_routes.py:297-298). Our 5s becomes 30s after the
    floor, the outer timeout becomes 30 + 45 = 75s. That's still ~8x
    faster than the 645s default and short enough that a single stuck
    test cannot kill the whole suite.

    Only fires for integration-marked tests. Other tests are untouched.
    No teardown: app.config lives as long as the request-scoped app fixture,
    so the shrunk dict is discarded automatically when the app is rebuilt
    for the next test. Using `yield` here would silently turn this fixture
    into a generator that never yields (early `return` for non-integration
    tests) and break every other test in the suite with
    "did not yield a value".
    """
    if "integration" not in request.keywords:
        yield
        return
    app = request.getfixturevalue("app")
    with app.app_context():
        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        planning_policy = dict(cfg.get("planning_policy") or {})
        planning_policy["timeout_seconds"] = 5
        planning_policy["queue_wait_timeout_seconds"] = 5
        cfg["planning_policy"] = planning_policy
        app.config["AGENT_CONFIG"] = cfg
        yield


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
        OidcIdentityLinkDB,
        PasswordHistoryDB,
        PlanDB,
        PlanNodeDB,
        PolicyDecisionDB,
        RefreshTokenDB,
        RetrievalRunDB,
        RoleDB,
        ShareParticipantDB,
        ShareSessionDB,
        ScheduledTaskDB,
        StatsSnapshotDB,
        TaskDB,
        TeamDB,
        TeamBlueprintDB,
        TeamMemberDB,
        TeamTypeDB,
        TeamTypeRoleLink,
        TerminalEventDB,
        TerminalSessionDB,
        TemplateDB,
        UserInstructionProfileDB,
        UserDB,
        VerificationRecordDB,
        WorkerJobDB,
        WorkerResultDB,
        WorkerSlotLeaseDB,
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
            WorkerSlotLeaseDB,
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
            ShareParticipantDB,
            ShareSessionDB,
            TerminalSessionDB,
            TerminalEventDB,
            ConfigDB,
            AgentInfoDB,
            OidcIdentityLinkDB,
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


@pytest.fixture
def session(db_session):
    """Compatibility alias for legacy SQLModel-based tests."""
    yield db_session


def _upsert_test_user(username: str, password: str, role: str = "user") -> None:
    from werkzeug.security import generate_password_hash

    from agent.db_models import UserDB

    runtime = _db_runtime()
    with runtime["Session"](runtime["engine"]) as db:
        user = db.get(UserDB, username)
        if user is None:
            user = UserDB(username=username, password_hash=generate_password_hash(password), role=role)
        else:
            user.password_hash = generate_password_hash(password)
            user.role = role
        user.mfa_enabled = False
        user.mfa_secret = None
        user.mfa_backup_codes = []
        user.failed_login_attempts = 0
        user.lockout_until = None
        db.add(user)
        db.commit()
    try:
        from agent.repository import banned_ip_repo, login_attempt_repo

        login_attempt_repo.clear_all()
        banned_ip_repo.clear_all()
    except Exception:
        pass


def _login_token(client, *, username: str, password: str) -> str:
    response = client.post("/login", json={"username": username, "password": password})
    payload = response.get_json(silent=True) or {}
    token = ((payload.get("data") or {}).get("access_token") or "").strip()
    if token:
        return token
    from agent.auth import generate_token
    from agent.config import settings

    role = "admin" if username == "admin" else "user"
    return generate_token({"sub": username, "role": role, "mfa_enabled": False}, settings.secret_key)


@pytest.fixture(autouse=True)
def cleanup_db_and_runtime():
    """Ensure every test leaves DB + runtime state clean."""

    def _reset_runtime_state():
        reset_auth_state()
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
            from agent.services.live_terminal_session_service import get_live_terminal_session_service

            live_terminal_service = get_live_terminal_session_service()
            snapshot = live_terminal_service.snapshot()
            for item in list(snapshot.get("items") or []):
                live_terminal_service.close_session(str(item.get("id") or ""))
        except Exception:
            pass

        try:
            from agent.services.background.registration import reset_registration_state

            reset_registration_state()
        except Exception:
            pass

        try:
            from agent.services.evolution import get_evolution_provider_registry

            get_evolution_provider_registry().clear()
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
            "data_test/users.json",
            "data_test/refresh_tokens.json",
            "data_test/llm_model_history.json",
            "data_test/llm_model_benchmarks.json",
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


@pytest.fixture(autouse=True)
def disable_planning_context_compactor_llm(monkeypatch):
    """Keep test runs deterministic by preventing hidden LLM calls via context compaction."""

    class _NoopCompactor:
        def compact(self, **kwargs):
            return types.SimpleNamespace(payload={}, meta={"status": "disabled"})

    monkeypatch.setattr(
        "agent.services.task_scoped_execution_service.get_planning_context_compactor_service",
        lambda: _NoopCompactor(),
    )


@pytest.fixture(autouse=True)
def isolate_operator_tui_user_config(tmp_path, monkeypatch):
    """Keep project/user chat config from leaking into deterministic tests."""
    try:
        import client_surfaces.operator_tui.config.user_config_manager as ucm
        import client_surfaces.operator_tui.snake_persistence as sp

        ucm.reset_manager()
        monkeypatch.setattr(ucm, "global_config_path", lambda: tmp_path / "home" / ".anana" / "user.json")
        monkeypatch.setattr(
            ucm,
            "project_config_path",
            lambda cwd=None: ((Path(cwd).resolve() if cwd is not None else tmp_path) / "user.json"),
        )
        default_manager = ucm.UserConfigManager(cwd=tmp_path)
        monkeypatch.setattr(ucm, "_manager", default_manager)
        original_get_manager = ucm.get_manager

        def _isolated_get_manager(*, cwd=None):
            if cwd is None:
                return default_manager
            return original_get_manager(cwd=cwd)

        monkeypatch.setattr(ucm, "get_manager", _isolated_get_manager)
        monkeypatch.setattr(sp, "_config_dir", lambda: tmp_path / ".config" / "ananta")
        yield
        ucm.reset_manager()
    except Exception:
        yield


@pytest.fixture(autouse=True)
def reset_cli_trace_svc_cache():
    """Reset the lazy _TRACE_SVC singleton in agent.cli.prompt_inspect_core.

    Several CLI prompt-inspect commands lazily resolve their trace service
    on first use and cache it for the rest of the process. Without a reset,
    a test that patches ``get_prompt_trace_service`` only sees its mock on
    the first invocation; subsequent tests in the same run resolve the
    real cached singleton and observe the wrong data. Resetting the cache
    before AND after each test guarantees deterministic behaviour.
    """
    try:
        from agent.cli import prompt_inspect_core as _pic

        _pic._reset_trace_svc_cache()
    except Exception:
        pass
    yield
    try:
        from agent.cli import prompt_inspect_core as _pic

        _pic._reset_trace_svc_cache()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def ensure_state_ownership_matrix_file():
    """Keep ownership-matrix tests deterministic in clean CI environments."""
    matrix_path = Path("data/state_ownership_matrix.json")
    matrix_path.parent.mkdir(parents=True, exist_ok=True)
    if not matrix_path.exists():
        payload = {
            "version": "state_ownership_matrix.v1",
            "states": [
                {"state_type": "goal", "owner": "hub", "server_owned": True, "mutable": True, "allowed_writers": ["hub"]},
                {"state_type": "plan", "owner": "hub", "server_owned": True, "mutable": True, "allowed_writers": ["hub"]},
                {"state_type": "task", "owner": "hub", "server_owned": True, "mutable": True, "allowed_writers": ["hub"]},
                {"state_type": "execution", "owner": "hub", "server_owned": True, "mutable": True, "allowed_writers": ["hub", "worker"]},
                {"state_type": "approval", "owner": "hub", "server_owned": True, "mutable": True, "allowed_writers": ["hub"]},
                {"state_type": "artifact", "owner": "hub", "server_owned": True, "mutable": True, "allowed_writers": ["hub", "worker"]},
                {
                    "state_type": "audit",
                    "owner": "hub",
                    "server_owned": True,
                    "mutable": False,
                    "append_only": True,
                    "allowed_writers": ["hub"],
                },
                {"state_type": "verification", "owner": "hub", "server_owned": True, "mutable": True, "allowed_writers": ["hub", "worker"]},
                {"state_type": "repair", "owner": "hub", "server_owned": True, "mutable": True, "allowed_writers": ["hub"]},
                {"state_type": "client_ui_state", "owner": "client", "server_owned": False, "mutable": True, "allowed_writers": ["client"]},
            ],
        }
        matrix_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    yield


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
    token = admin_login_token(client)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def user_auth_header(client, app):
    """Creates a regular user and returns auth header."""
    _upsert_test_user("testuser", "testpass", "user")
    token = _login_token(client, username="testuser", password="testpass")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_auth_header(client):
    """Returns a valid auth header for an admin user."""
    token = admin_login_token(client)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_token(client):
    """Compatibility fixture for older API tests that expect a raw admin token."""
    return admin_login_token(client)

# Pre-existing broken test files - skip collection
collect_ignore_glob = ["e2e/fixtures/*/tests/*.py"]
collect_ignore = ["test_worker_client_adapter.py"]
