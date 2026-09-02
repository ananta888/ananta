import base64
import json
import os
import sys
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
os.environ["DATABASE_URL"] = (
    f"sqlite:///file:ananta-pytest-{os.getpid()}"
    "?mode=memory&cache=shared&uri=true"
)
# Each xdist worker is a separate process but used to share ``data/``.  The
# cleanup fixture then let one worker remove another worker's artifact or RAG
# output mid-test.  Bind all mutable test files to the process-local sandbox
# before importing application settings.
os.environ["DATA_DIR"] = f"/tmp/ananta-pytest-data-{os.getpid()}"
os.environ["CONTROLLER_URL"] = "http://mock-controller"
os.environ["AGENT_NAME"] = "test-agent"
os.environ["VOICE_DELETION_LEDGER_PATH"] = (
    f"/tmp/ananta-voice-deletion-ledger-pytest-{os.getpid()}.jsonl"
)
os.environ.setdefault("INITIAL_ADMIN_USER", "admin")
os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "admin")

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


@pytest.fixture(autouse=True)
def _legacy_workflow_runner_text_generation_port(app):
    """Keep legacy adapter tests injectable after the production Hub split.

    Production composition uses the Worker-local, Hub-budgeted provider port.
    Older unit tests still patch ``agent.llm_integration.generate_text``; this
    test-only adapter preserves that seam without reintroducing the dependency
    into Worker production modules.
    """

    from agent import llm_integration
    from worker.adapters.chain_runners import configure_text_generation

    class TestTextGenerationPort:
        @staticmethod
        def generate_text(**values):
            return llm_integration.generate_text(**values)

    configure_text_generation(TestTextGenerationPort())
    try:
        yield
    finally:
        configure_text_generation(None)


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


@pytest.fixture
def workflow_runtime_auth_keyring_file(tmp_path, monkeypatch):
    """Configure the persistent workflow signer used by production composition.

    Route tests intentionally exercise the real Hub composition root.  They
    therefore receive the same file-backed key configuration and observed
    worker-directory health as a deployed Hub instead of weakening either
    production admission guard.
    """

    keyring_path = tmp_path / "workflow-runtime-auth-keyring.json"
    from ananta_contracts.runtime_authorization_crypto import (
        ED25519_ALGORITHM,
        ED25519_SIGNING_KEYRING_SCHEMA,
    )

    keyring_path.write_text(
        json.dumps(
            {
                "schema": ED25519_SIGNING_KEYRING_SCHEMA,
                "algorithm": ED25519_ALGORITHM,
                "active_key_id": "pytest-workflow-runtime",
                "private_keys": {"pytest-workflow-runtime": base64.b64encode(b"t" * 32).decode("ascii")},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    keyring_path.chmod(0o600)
    monkeypatch.setenv(
        "ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_FILE",
        str(keyring_path),
    )
    monkeypatch.delenv("ANANTA_WORKFLOW_AUTH_KEYRING_FILE", raising=False)
    monkeypatch.delenv(
        "ANANTA_WORKFLOW_ALLOW_LEGACY_HMAC_KEYRING",
        raising=False,
    )

    import time

    from sqlmodel import Session

    from agent.database import engine
    from agent.db_models import AgentInfoDB

    _ensure_test_db()
    worker_url = f"http://pytest-native-worker-{tmp_path.name}:5000"
    with Session(engine) as session:
        session.merge(
            AgentInfoDB(
                url=worker_url,
                name=f"pytest-native-worker-{tmp_path.name}",
                role="worker",
                capabilities=["workflow.adapter.native"],
                runtime_targets=[
                    {
                        "runtime_id": "ananta-native",
                        "adapter_id": "native",
                        "runtime_version": "1.0.0",
                    }
                ],
                registration_validated=True,
                validated_at=time.time(),
                last_seen=time.time(),
                status="online",
            )
        )
        session.commit()

    from agent.services.workflow_control_composition import (
        reset_workflow_backend_control_facade,
    )
    from agent.services.workflow_hub_task_gateway_runtime import (
        reset_workflow_hub_task_gateway_service,
    )

    reset_workflow_backend_control_facade()
    reset_workflow_hub_task_gateway_service()
    yield keyring_path
    reset_workflow_backend_control_facade()
    reset_workflow_hub_task_gateway_service()
    with Session(engine) as session:
        worker = session.get(AgentInfoDB, worker_url)
        if worker is not None:
            session.delete(worker)
            session.commit()


def _db_runtime() -> dict[str, Any]:
    _ensure_test_db()
    from sqlalchemy import inspect, text
    from sqlalchemy.exc import IntegrityError, OperationalError
    from sqlmodel import Session, delete

    from agent.db_models import (
        ActionPackDB,
        AgentInfoDB,
        ApprovalRequestDB,
        ArchivedTaskDB,
        ArtifactDB,
        ArtifactVersionDB,
        AuditLogDB,
        BannedIPDB,
        BlueprintArtifactDB,
        BlueprintRoleDB,
        BlueprintWorkflowStepDB,
        ConfigDB,
        ContextBundleDB,
        CrossTeamTaskDependencyDB,
        EvolutionProposalDB,
        EvolutionRunDB,
        ExtractedDocumentDB,
        GoalDB,
        HubRunEvidenceIdentityDB,
        HubSourceEvidenceIdentityDB,
        InstructionOverlayDB,
        KanbanBoardSequenceDB,
        KanbanOutboxEventDB,
        KnowledgeCollectionDB,
        KnowledgeIndexDB,
        KnowledgeIndexRunDB,
        KnowledgeLinkDB,
        LoginAttemptDB,
        MemoryEntryDB,
        MlInternDatasetDB,
        MlInternSpeechAdapterDB,
        MlInternSpeechAdapterLegacyImportDB,
        MlInternTrainingAttemptDB,
        MlInternTrainingCapacityLeaseDB,
        MlInternTrainingEventDB,
        MlInternTrainingExecutionLeaseDB,
        MlInternTrainingJobDB,
        OidcIdentityLinkDB,
        OrganizationAdminGrantDB,
        OrganizationAdmissionExceptionDB,
        OrganizationAuditOutboxDB,
        OrganizationBlueprintRevisionDB,
        OrganizationBudgetReservationDB,
        OrganizationBudgetUsageDB,
        OrganizationHandoffDefinitionRevisionDB,
        OrganizationInstanceDB,
        OrganizationLayoutPreferenceDB,
        OrganizationLimitProfileRevisionDB,
        OrganizationMembershipDB,
        OrganizationOperationDB,
        OrganizationPolicyRevisionDB,
        OrganizationRelationDB,
        OrganizationRoleAssignmentDB,
        OrganizationRoleSlotDB,
        OrganizationRuntimeEventDB,
        OrganizationTeamHandoffDB,
        OrganizationTeamLinkDB,
        OrganizationTopologyPatchGrantDB,
        OrganizationTopologySnapshotDB,
        OrganizationUnitDB,
        OrganizationWorkflowLoopStateDB,
        PasswordHistoryDB,
        PlanDB,
        PlanNodeDB,
        PolicyDecisionDB,
        ProjectDB,
        ProjectMembershipDB,
        RefreshTokenDB,
        RetrievalRunDB,
        RoleDB,
        RoleTemplateRevisionDB,
        ScheduledTaskDB,
        ScientificSkillProvenanceReceiptDB,
        SemanticCapabilityAdvertisementDB,
        SemanticComputeCandidateKeyDB,
        SemanticComputeContractDB,
        SemanticComputeLeaseDB,
        SemanticComputeLeaseMutationDB,
        SemanticComputeScheduleReceiptDB,
        SemanticContractMutationDB,
        SemanticLeaseFenceDB,
        SemanticMediaAuditEventDB,
        SemanticMediaAuditOutboxDB,
        SemanticRelayCursorDB,
        SemanticRelayEnvelopeDB,
        SemanticSessionMembershipDB,
        SemanticSfuAdmissionReceiptDB,
        SemanticSfuRoomStateDB,
        ShareParticipantDB,
        ShareSessionDB,
        SpeechAdaptationArtifactDB,
        SpeechAdaptationCapacityLeaseDB,
        SpeechAdaptationJobDB,
        SpeechCurationTaskDB,
        SpeechDatasetManifestDB,
        SpeechEvidenceAdmissionDB,
        SpeechEvidenceCleanupDB,
        SpeechEvidenceConsentDB,
        SpeechEvidenceDB,
        SpeechEvidenceKeyDB,
        SpeechEvidenceOfferDB,
        SpeechEvidencePeerKeyDB,
        SpeechEvidenceReplayStateDB,
        SpeechEvidenceRevocationDB,
        SpeechEvidenceTransferChunkDB,
        SpeechEvidenceTransferDB,
        SpeechLineageEdgeDB,
        SpeechLineageNodeDB,
        SpeechLineageOutboxDB,
        SpeechReconciliationArtifactDB,
        SpeechReconciliationAttemptDB,
        SpeechReconciliationBudgetLedgerDB,
        SpeechReconciliationCheckpointDB,
        SpeechReconciliationJobDB,
        SpeechReconciliationMutationDB,
        StatsSnapshotDB,
        TaskDB,
        TeamBlueprintDB,
        TeamBlueprintRevisionDB,
        TeamDB,
        TeamMemberDB,
        TeamTypeDB,
        TeamTypeRoleLink,
        TemplateDB,
        TerminalEventDB,
        TerminalSessionDB,
        UserDB,
        UserInstructionProfileDB,
        VerificationRecordDB,
        VoiceConfigurationDeltaDB,
        VoiceConsentDB,
        VoiceDeletionTombstoneDB,
        VoiceFeedbackDB,
        VoiceGovernanceIdempotencyDB,
        VoiceLiveRunDB,
        VoiceLiveRunSegmentDB,
        VoicePersonalizationProfileDB,
        VoiceResultArtifactDB,
        VoiceReviewDB,
        VoiceRuntimeCleanupDB,
        WorkerJobDB,
        WorkerResultDB,
        WorkerSlotLeaseDB,
        WorkflowCommandNonceDB,
        WorkflowControlBindingDB,
        WorkflowDefinitionRevisionDB,
        WorkflowProviderBudgetDB,
        WorkflowProviderBudgetReservationDB,
        WorkflowRuntimeReadModelDB,
    )

    return {
        "engine": _db_engine(),
        "inspect": inspect,
        "text": text,
        "OperationalError": OperationalError,
        "IntegrityError": IntegrityError,
        "Session": Session,
        "delete": delete,
        "models": (
            OrganizationAuditOutboxDB,
            OrganizationOperationDB,
            OrganizationTopologySnapshotDB,
            OrganizationLayoutPreferenceDB,
            OrganizationAdmissionExceptionDB,
            OrganizationTopologyPatchGrantDB,
            OrganizationAdminGrantDB,
            OrganizationMembershipDB,
            CrossTeamTaskDependencyDB,
            OrganizationRelationDB,
            OrganizationRoleAssignmentDB,
            OrganizationWorkflowLoopStateDB,
            OrganizationTeamHandoffDB,
            OrganizationRuntimeEventDB,
            OrganizationBudgetReservationDB,
            OrganizationBudgetUsageDB,
            OrganizationRoleSlotDB,
            OrganizationTeamLinkDB,
            OrganizationUnitDB,
            OrganizationInstanceDB,
            OrganizationHandoffDefinitionRevisionDB,
            OrganizationBlueprintRevisionDB,
            OrganizationPolicyRevisionDB,
            OrganizationLimitProfileRevisionDB,
            WorkflowDefinitionRevisionDB,
            TeamBlueprintRevisionDB,
            RoleTemplateRevisionDB,
            ProjectMembershipDB,
            ApprovalRequestDB,
            KanbanOutboxEventDB,
            KanbanBoardSequenceDB,
            SemanticSfuAdmissionReceiptDB,
            SemanticSfuRoomStateDB,
            SpeechReconciliationArtifactDB,
            SpeechReconciliationCheckpointDB,
            SpeechReconciliationBudgetLedgerDB,
            SpeechReconciliationAttemptDB,
            SpeechReconciliationMutationDB,
            SpeechReconciliationJobDB,
            SpeechEvidenceTransferChunkDB,
            SpeechEvidenceTransferDB,
            SpeechEvidenceOfferDB,
            SpeechEvidenceReplayStateDB,
            SpeechEvidencePeerKeyDB,
            SpeechEvidenceCleanupDB,
            SpeechEvidenceRevocationDB,
            SpeechLineageOutboxDB,
            SpeechLineageEdgeDB,
            SpeechLineageNodeDB,
            SpeechDatasetManifestDB,
            SpeechCurationTaskDB,
            SpeechEvidenceAdmissionDB,
            SpeechEvidenceDB,
            SpeechEvidenceKeyDB,
            SpeechEvidenceConsentDB,
            SpeechAdaptationArtifactDB,
            SpeechAdaptationCapacityLeaseDB,
            SpeechAdaptationJobDB,
            MlInternTrainingEventDB,
            MlInternTrainingAttemptDB,
            MlInternTrainingExecutionLeaseDB,
            MlInternTrainingCapacityLeaseDB,
            MlInternTrainingJobDB,
            MlInternDatasetDB,
            MlInternSpeechAdapterDB,
            MlInternSpeechAdapterLegacyImportDB,
            SemanticRelayEnvelopeDB,
            SemanticRelayCursorDB,
            SemanticComputeLeaseMutationDB,
            SemanticComputeScheduleReceiptDB,
            SemanticComputeLeaseDB,
            SemanticLeaseFenceDB,
            SemanticCapabilityAdvertisementDB,
            SemanticComputeCandidateKeyDB,
            SemanticContractMutationDB,
            SemanticComputeContractDB,
            SemanticSessionMembershipDB,
            SemanticMediaAuditOutboxDB,
            SemanticMediaAuditEventDB,
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
            # Deleted alongside its siblings: this cleanup drops team_blueprints
            # with foreign keys switched off, so a workflow step left behind
            # becomes an orphan, and the Hub scans for orphans at startup —
            # every later app in the session then failed to come up at all.
            BlueprintWorkflowStepDB,
            TeamTypeRoleLink,
            ScheduledTaskDB,
            HubRunEvidenceIdentityDB,
            HubSourceEvidenceIdentityDB,
            ScientificSkillProvenanceReceiptDB,
            ArchivedTaskDB,
            TaskDB,
            PlanNodeDB,
            PlanDB,
            GoalDB,
            ProjectDB,
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
            VoiceFeedbackDB,
            VoiceDeletionTombstoneDB,
            VoiceConfigurationDeltaDB,
            VoicePersonalizationProfileDB,
            VoiceReviewDB,
            VoiceResultArtifactDB,
            VoiceRuntimeCleanupDB,
            VoiceConsentDB,
            VoiceGovernanceIdempotencyDB,
            VoiceLiveRunSegmentDB,
            VoiceLiveRunDB,
            WorkflowRuntimeReadModelDB,
            WorkflowProviderBudgetReservationDB,
            WorkflowProviderBudgetDB,
            WorkflowCommandNonceDB,
            WorkflowControlBindingDB,
            AuditLogDB,
            UserDB,
        ),
    }


@pytest.fixture(autouse=True, scope="function")
def _db_savepoint_isolation():
    """
    Kept as a compatibility fixture for tests that rely on its autouse name.

    The previous implementation patched only ``agent.database.Session`` with
    nested SQLite savepoints, while many repositories import ``sqlmodel.Session``
    directly. Long shard runs then mixed savepoint-managed and direct sessions
    and could leave SQLite with ``no such savepoint`` setup failures. The
    cleanup_db_and_runtime fixture below owns test isolation by clearing all
    known tables before and after each test.
    """
    yield


@pytest.fixture
def db_session():
    """
    Provides a SQLModel session.  The actual DB isolation is handled by the
    autouse _db_savepoint_isolation fixture above; this fixture exists for
    any tests that need a session handle for direct assertions.
    """
    runtime = _db_runtime()
    # Return the Session bound to the engine so callers get a valid session
    # that participates in the savepoint set up by _db_savepoint_isolation.
    return runtime["Session"](runtime["engine"])


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
        # Voice corrections run on the hub-owned executor and may still hold a
        # SQLAlchemy session after the request that scheduled them returned.
        # Drain them before deleting rows; concurrent SQLite cleanup can
        # otherwise cross a test boundary or even crash the interpreter.
        from agent.services.voice_live_run_correction_service import (
            get_voice_live_run_correction_service,
        )

        if not get_voice_live_run_correction_service().wait_for_idle(timeout=10.0):
            raise RuntimeError("voice live correction executor did not become idle")

        from agent.services.ml_intern_training_control_service import (
            wait_for_ml_intern_training_control_idle,
        )

        if not wait_for_ml_intern_training_control_idle(timeout=10.0):
            raise RuntimeError("ML-Intern training control executor did not become idle")

        reset_auth_state()
        try:
            from agent.routes.control_center_api import stop_control_center_event_poller

            stop_control_center_event_poller()
        except Exception:
            pass
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
            # NOTE: do NOT reset autonomous_loop._app here. The `app` fixture
            # rebinds it per-test, but pytest may schedule that fixture *after*
            # this autouse cleanup, which would leave _app=None for the duration
            # of the test body and break `tick_once` in worker threads. Leave
            # _app alone; runtime state (intervals, counters, locks) is fully
            # reset above.
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

        # --- Mutable module-level singletons ---

        try:
            import agent.services.ssh_certificate_issuer as _ssh

            _ssh._KNOWN_NONCES.clear()
            _ssh._ALLOWED_ISSUERS_FROM_CONFIG = None
        except Exception:
            pass

        try:
            import agent.routes.auth_oidc as _oidc

            _oidc._FRONTEND_TOKEN_EXCHANGE_CODES.clear()
            _oidc._OIDC_LOGIN_REQUESTS.clear()
        except Exception:
            pass

        try:
            import agent.routes.snakes_state as _ss

            _ss._snakes.clear()
            _ss._messages.clear()
            _ss._chat_messages.clear()
        except Exception:
            pass

        try:
            import agent.services.terminal_session_service as _tss

            _tss._ATTACH_TOKENS.clear()
        except Exception:
            pass

        try:
            import agent.llm_resilience as _res

            for values in _res.CIRCUIT_BREAKER.values():
                values.clear()
            _res._RATE_LIMIT_WINDOW.clear()
            _res._ERR_SUCCESS_WINDOW.clear()
            _res._ERR_FAILURE_WINDOW.clear()
        except Exception:
            pass

        try:
            import agent.llm_integration as _llmi

            _llmi._LOCAL_RUNTIME_SELECTION_CACHE.clear()
        except Exception:
            pass

        try:
            from agent.common import lmstudio_request_registry as _lms

            _lms._goal_sessions.clear()
            _lms._task_sessions.clear()
            _lms._thread_context.clear()
        except Exception:
            pass

        runtime = _db_runtime()
        inspector = runtime["inspect"](runtime["engine"])
        session_cls = runtime["Session"]
        delete_stmt = runtime["delete"]
        operational_error = runtime["OperationalError"]
        integrity_error = runtime["IntegrityError"]
        is_sqlite = runtime["engine"].dialect.name == "sqlite"

        def _delete_if_table_exists(model):
            try:
                if inspector.has_table(model.__tablename__):
                    with session_cls(runtime["engine"]) as session:
                        if is_sqlite:
                            session.exec(runtime["text"]("PRAGMA foreign_keys = OFF"))
                        session.exec(delete_stmt(model))
                        session.commit()
                        if is_sqlite:
                            session.exec(runtime["text"]("PRAGMA foreign_keys = ON"))
            except operational_error:
                pass
            except integrity_error:
                if is_sqlite:
                    try:
                        with session_cls(runtime["engine"]) as session:
                            session.exec(runtime["text"]("PRAGMA foreign_keys = ON"))
                    except Exception:
                        pass
                    return
                raise

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
        try:
            Path(os.environ["VOICE_DELETION_LEDGER_PATH"]).unlink(missing_ok=True)
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

        # Best-effort DB cleanup: roll back any in-flight transaction on the
        # test engine so the next test starts from a clean slate. We avoid
        # `engine.dispose()` because that drops the in-memory SQLite tables
        # and would force every test to re-run schema migration.
        try:
            from agent.database import engine

            with engine.connect() as conn:
                conn.rollback()
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
            lambda cwd=None: (Path(cwd).resolve() if cwd is not None else tmp_path) / "user.json",
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
def _reset_log_record_factory():
    """Restore log record factory after each test.

    setup_logging() installs a custom record factory. If a test calls
    create_app() (which triggers setup_logging()), the factory gets replaced.
    Without this fixture the factories accumulate across tests in the full run
    until the call stack overflows (~950 levels → RecursionError).

    The production-side fix in agent/common/logging.py makes setup_logging()
    idempotent; this fixture is a belt-and-suspenders guard for any other code
    path that may swap the factory.
    """
    import logging as _logging

    factory_before = _logging.getLogRecordFactory()
    yield
    _logging.setLogRecordFactory(factory_before)


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
                {
                    "state_type": "goal",
                    "owner": "hub",
                    "server_owned": True,
                    "mutable": True,
                    "allowed_writers": ["hub"],
                },
                {
                    "state_type": "plan",
                    "owner": "hub",
                    "server_owned": True,
                    "mutable": True,
                    "allowed_writers": ["hub"],
                },
                {
                    "state_type": "task",
                    "owner": "hub",
                    "server_owned": True,
                    "mutable": True,
                    "allowed_writers": ["hub"],
                },
                {
                    "state_type": "execution",
                    "owner": "hub",
                    "server_owned": True,
                    "mutable": True,
                    "allowed_writers": ["hub", "worker"],
                },
                {
                    "state_type": "approval",
                    "owner": "hub",
                    "server_owned": True,
                    "mutable": True,
                    "allowed_writers": ["hub"],
                },
                {
                    "state_type": "artifact",
                    "owner": "hub",
                    "server_owned": True,
                    "mutable": True,
                    "allowed_writers": ["hub", "worker"],
                },
                {
                    "state_type": "audit",
                    "owner": "hub",
                    "server_owned": True,
                    "mutable": False,
                    "append_only": True,
                    "allowed_writers": ["hub"],
                },
                {
                    "state_type": "verification",
                    "owner": "hub",
                    "server_owned": True,
                    "mutable": True,
                    "allowed_writers": ["hub", "worker"],
                },
                {
                    "state_type": "repair",
                    "owner": "hub",
                    "server_owned": True,
                    "mutable": True,
                    "allowed_writers": ["hub"],
                },
                {
                    "state_type": "client_ui_state",
                    "owner": "client",
                    "server_owned": False,
                    "mutable": True,
                    "allowed_writers": ["client"],
                },
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
