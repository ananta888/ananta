import logging
import os
import time

from flask import Flask

from agent import db_models as _  # noqa: F401 - register SQLModel tables before init_db
from agent.bootstrap.background import start_background_services
from agent.bootstrap.extensions import configure_cors, configure_swagger, load_extensions
from agent.bootstrap.request_hooks import configure_audit_logger, register_request_hooks
from agent.bootstrap.routes import register_alias_routes, register_blueprints
from agent.bootstrap.runtime_hints import log_runtime_hints
from agent.bootstrap.startup import run_startup_phase
from agent.bootstrap.voice_runtime_cleanup import recover_voice_runtime_cleanup
from agent.common.error_handler import register_error_handler
from agent.common.logging import setup_logging
from agent.common.signals import setup_signal_handlers
from agent.config import settings
from agent.database import init_db
from agent.metrics import APP_STARTUP_DURATION
from agent.services.app_runtime_service import build_base_app_config, initialize_runtime_state
from agent.services.deterministic_repair_handler import DeterministicRepairHandler
from agent.services.repository_registry import initialize_repository_registry
from agent.services.run_tests_handler import register_run_tests_handler
from agent.services.service_registry import initialize_core_services
from agent.services.task_handler_registry import register_task_handler
from agent.utils import read_json
from agent.utils import register_with_hub as _register_with_hub
from worker.core.template_propose_handler import TemplateProposeHandler

register_with_hub = _register_with_hub

_configure_audit_logger = configure_audit_logger
_configure_cors = configure_cors
_configure_swagger = configure_swagger
_load_extensions = load_extensions
_log_runtime_hints = log_runtime_hints
_register_alias_routes = register_alias_routes
_register_blueprints = register_blueprints
_register_request_hooks = register_request_hooks
_start_background_services = start_background_services


def _initialize_workflow_adapter_worker_runtime(app: Flask):
    if settings.role != "worker":
        app.extensions["workflow_adapter_worker_registration"] = {
            "capabilities": [],
            "runtime_targets": [],
            "reason_codes": ["workflow_adapter_worker_role_required"],
        }
        return None
    from worker.adapters.chain_runners import configure_text_generation
    from worker.runtime.native_graph.authorization import (
        HubBackedNativeAuthorizationVerifier,
        load_ed25519_native_authorization_verifier,
    )
    from worker.runtime.native_graph.composition import TaskScopedNativeWorkerExecutor
    from worker.runtime.native_worker_runtime_service import (
        get_native_worker_runtime_service,
    )
    from worker.runtime.provider_text_generation import (
        build_hub_budgeted_worker_text_generation,
    )
    from worker.runtime.workflow_adapter_runtime_composition import (
        initialize_workflow_adapter_worker_runtime,
    )
    from worker.runtime.workflow_hub_gateway import HttpWorkflowHubDecisionClient
    from worker.runtime.workspace_resolver import (
        ConfiguredWorkerWorkspaceResolver,
        WorkerWorkspaceResolutionError,
    )

    agent_config = dict(app.config.get("AGENT_CONFIG") or {})
    try:
        client = HttpWorkflowHubDecisionClient.from_environment()
    except ValueError:
        client = None
    configure_text_generation(
        build_hub_budgeted_worker_text_generation(
            client=client,
            provider_urls=dict(app.config.get("PROVIDER_URLS") or {}),
        )
        if client is not None
        else None
    )
    native_executor = None
    try:
        workspaces = ConfiguredWorkerWorkspaceResolver(agent_config)
    except WorkerWorkspaceResolutionError:
        pass
    else:
        native_executor = TaskScopedNativeWorkerExecutor(
            runtime=get_native_worker_runtime_service(),
            workspaces=workspaces,
        )
    try:
        native_authorization_verifier = (
            load_ed25519_native_authorization_verifier()
            or HubBackedNativeAuthorizationVerifier()
        )
    except ValueError:
        native_authorization_verifier = None

    return initialize_workflow_adapter_worker_runtime(
        app,
        client=client,
        native_executor=native_executor,
        native_authorization_verifier=native_authorization_verifier,
    )


def _should_skip_threads_for_reloader() -> bool:
    from agent.lifecycle import BackgroundServiceManager

    return BackgroundServiceManager(object())._should_skip_for_reloader()


def _register_deterministic_repair_handler(app: Flask) -> None:
    handler = DeterministicRepairHandler()
    register_task_handler(
        "admin_repair",
        handler,
        app=app,
        capabilities=["deterministic_repair", "shell_execute"],
        safety_flags={"requires_review": True, "requires_approval": True},
        verification_hooks=["step_verification", "final_verification"],
    )
    register_task_handler(
        "deterministic_repair",
        handler,
        app=app,
        capabilities=["deterministic_repair", "shell_execute"],
        safety_flags={"requires_review": True},
        verification_hooks=["step_verification"],
    )


def _register_template_propose_handler(app: Flask) -> None:
    handler = TemplateProposeHandler()
    for kind in ("new_software_project", "coding"):
        register_task_handler(
            kind,
            handler,
            app=app,
            capabilities=["template_propose", "shell_execute"],
            safety_flags={"requires_review": False},
        )


def _check_token_rotation(app: Flask) -> None:
    """Backward-compatible token-rotation check used by legacy tests."""
    if str((app.config or {}).get("AGENT_TOKEN_FILE") or settings.agent_token_file or "").strip():
        return
    token_path = str((app.config or {}).get("TOKEN_PATH") or settings.token_path or "").strip()
    if not token_path or not os.path.exists(token_path):
        return
    try:
        token_data = read_json(token_path) or {}
        last_rotation = float(token_data.get("last_rotation") or 0)
        rotation_interval = int(settings.token_rotation_days or 7) * 86400
        if time.time() - last_rotation > rotation_interval:
            logging.info("Token-Rotations-Intervall erreicht. Starte Rotation...")
            with app.app_context():
                from agent.auth import rotate_token

                rotate_token()
    except Exception as exc:
        logging.error(f"Fehler bei der Prüfung der Token-Rotation: {exc}")


def _validate_workflow_credential_boundary(app: Flask) -> None:
    """Fail startup when a credential crosses a workflow trust boundary."""

    from agent.auth import resolve_configured_agent_token
    from agent.services.repository_registry import get_repository_registry
    from agent.services.workflow_worker_service_auth import (
        registered_worker_auth_required,
        runtime_service_keyring_configured,
        validate_workflow_credential_disjointness,
    )

    if not registered_worker_auth_required(
        app.config
    ) and not runtime_service_keyring_configured(app.config):
        return
    with app.app_context():
        agents = get_repository_registry(app).agent_repo.get_all() or ()
        validate_workflow_credential_disjointness(
            user_session_secret=app.secret_key,
            hub_service_token=resolve_configured_agent_token(app.config),
            worker_service_tokens=(
                str(getattr(agent, "token", "") or "") for agent in agents
            ),
            config=app.config,
        )


def create_app(agent: str = "default", *, testing: bool = False) -> Flask:
    """Erzeugt die Flask-App fuer den Agenten (API-Server)."""
    _start_perf = time.perf_counter()
    if not testing:
        run_startup_phase("logging", setup_logging, level=settings.log_level, json_format=settings.log_json)
        run_startup_phase("signals", setup_signal_handlers)
        run_startup_phase("runtime_hints", log_runtime_hints)
        run_startup_phase("audit_logger", configure_audit_logger)
        run_startup_phase("database", init_db)
        run_startup_phase("voice_runtime_cleanup_recovery", recover_voice_runtime_cleanup)

    app = run_startup_phase("flask_app", Flask, __name__)
    app.config["TESTING"] = testing # Set Flask's testing flag
    # Required for Flask session-backed auth flows (e.g., OIDC state/nonce storage).
    app.secret_key = settings.secret_key
    run_startup_phase("request_hooks", register_request_hooks, app)
    run_startup_phase("error_handlers", register_error_handler, app)
    run_startup_phase("cors", configure_cors, app)
    app.config.update(run_startup_phase("base_config", build_base_app_config, agent))
    run_startup_phase("swagger", configure_swagger, app)
    run_startup_phase("blueprints", register_blueprints, app)
    run_startup_phase("alias_routes", register_alias_routes, app)
    run_startup_phase("runtime_state", initialize_runtime_state, app)
    run_startup_phase("extensions", load_extensions, app)
    run_startup_phase("repository_registry", initialize_repository_registry, app)
    run_startup_phase(
        "workflow_credential_boundary",
        _validate_workflow_credential_boundary,
        app,
    )
    run_startup_phase("core_services", initialize_core_services, app)
    run_startup_phase(
        "workflow_adapter_worker_runtime",
        _initialize_workflow_adapter_worker_runtime,
        app,
    )
    if not testing: # Skip background services in testing mode
        run_startup_phase("background_services", start_background_services, app)

    run_startup_phase("deterministic_repair_handler", _register_deterministic_repair_handler, app)
    run_startup_phase("run_tests_handler", register_run_tests_handler, app)
    run_startup_phase("template_propose_handler", _register_template_propose_handler, app)

    if not testing:
        elapsed = time.perf_counter() - _start_perf
        APP_STARTUP_DURATION.set(elapsed)
        logging.info(f"Ananta Agent '{agent}' (role={settings.role}) started in {elapsed:.4f}s")

    return app



def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "")


if __name__ == "__main__":
    import sys

    if "--version" in sys.argv:
        from agent.config import settings

        print(f"Ananta Agent v{settings.version}")
        sys.exit(0)

    app = create_app()
    _debug = _env_flag("FLASK_DEBUG")
    _reload = _env_flag("FLASK_RELOAD") or _debug
    app.run(host="0.0.0.0", port=settings.port, threaded=True, debug=_debug, use_reloader=_reload)
