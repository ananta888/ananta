import logging
import os
import signal
import threading
import time
import uuid

from flask import Flask, request
from werkzeug.exceptions import HTTPException

try:
    from flask_cors import CORS
except ImportError:
    CORS = None

try:
    from flasgger import Swagger
except ImportError:
    Swagger = None

from agent.common.errors import (
    AnantaError,
    PermanentError,
    TransientError,
    api_response,
)
from agent.common.errors import (
    ValidationError as AnantaValidationError,
)
from agent.common.logging import JsonFormatter, get_correlation_id, set_correlation_id, setup_logging
from agent.common.signals import setup_signal_handlers
from agent.config import settings
from agent.database import OperationalError, init_db
from agent.routes.artifacts import artifacts_bp
from agent.routes.auth import auth_bp
from agent.routes.config import register_config_blueprints
from agent.routes.knowledge import knowledge_bp
from agent.routes.openai_compat import openai_compat_bp
from agent.routes.sgpt import sgpt_bp
from agent.routes.system import system_bp
from agent.routes.tasks import register_tasks_blueprints, tasks_bp
from agent.routes.teams import teams_bp
from agent.services.app_runtime_service import build_base_app_config, initialize_runtime_state
from agent.services.service_registry import initialize_core_services
from agent.utils import _archive_old_tasks, _archive_terminal_logs, _cleanup_old_backups, read_json, register_with_hub
from agent.ws_terminal import register_ws_terminal
from agent.common.error_handler import register_error_handler


def _is_truthy_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}




def _configure_audit_logger() -> None:
    audit_file = os.path.join(settings.data_dir, "audit.log")
    audit_handler = logging.FileHandler(audit_file, encoding="utf-8")
    if settings.log_json:
        audit_handler.setFormatter(JsonFormatter())
    else:
        audit_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))

    audit_logger = logging.getLogger("audit")
    audit_logger.setLevel(logging.INFO)
    audit_logger.addHandler(audit_handler)
    audit_logger.propagate = False


def _register_request_hooks(app: Flask) -> None:
    @app.before_request
    def ensure_correlation_id_and_check_shutdown():
        import agent.common.context

        cid = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        set_correlation_id(cid)
        if agent.common.context.shutdown_requested and request.endpoint not in (
            "system.health",
            "tasks.get_logs",
            "tasks.task_logs",
        ):
            return api_response(status="shutdown_in_progress", code=503)

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        default_csp = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "report-uri /api/system/csp-report;"
        )
        swagger_csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "report-uri /api/system/csp-report;"
        )
        swagger_paths = ("/apidocs", "/apispec", "/flasgger_static")
        csp = swagger_csp if request.path.startswith(swagger_paths) else default_csp
        response.headers.setdefault("Content-Security-Policy", csp)
        is_https = request.is_secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https"
        if is_https:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains; preload")
        return response




def _configure_cors(app: Flask) -> None:
    if not CORS:
        return
    try:
        origins = settings.cors_origins
        if "," in origins:
            origins = [o.strip() for o in origins.split(",")]
        CORS(app, resources={r"*": {"origins": origins}})
    except Exception as e:
        logging.error(f"CORS konnte nicht initialisiert werden: {e}")




def _configure_swagger(app: Flask) -> None:
    if not Swagger:
        return
    Swagger(
        app,
        template={
            "swagger": "2.0",
            "info": {
                "title": "Ananta Agent API",
                "description": "API Dokumentation fuer den Ananta Agenten",
                "version": "1.0.0",
            },
            "securityDefinitions": {
                "Bearer": {
                    "type": "apiKey",
                    "name": "Authorization",
                    "in": "header",
                    "description": "JWT Token im Format 'Bearer <token>'",
                }
            },
            "security": [{"Bearer": []}],
        },
    )


def _register_blueprints(app: Flask) -> None:
    app.register_blueprint(system_bp, url_prefix="/api/system")
    register_config_blueprints(app)
    app.register_blueprint(tasks_bp)
    register_tasks_blueprints(app)
    app.register_blueprint(artifacts_bp)
    app.register_blueprint(knowledge_bp)
    app.register_blueprint(openai_compat_bp)
    app.register_blueprint(teams_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(sgpt_bp, url_prefix="/api/sgpt")
    register_ws_terminal(app)


def _register_alias_routes(app: Flask) -> None:
    try:
        from agent.routes.system import (
            analyze_audit_logs,
            get_audit_logs,
            get_stats_history,
            health,
            list_agents,
            metrics,
            readiness_check,
            register_agent,
            stream_system_events,
            system_stats,
        )

        app.add_url_rule("/health", view_func=health)
        app.add_url_rule("/ready", view_func=readiness_check)
        app.add_url_rule("/metrics", view_func=metrics)
        app.add_url_rule("/stats", view_func=system_stats)
        app.add_url_rule("/stats/history", view_func=get_stats_history)
        app.add_url_rule("/events", view_func=stream_system_events)
        app.add_url_rule("/agents", view_func=list_agents)
        app.add_url_rule("/audit-logs", view_func=get_audit_logs)
        app.add_url_rule("/audit/analyze", view_func=analyze_audit_logs, methods=["POST"])
        app.add_url_rule("/register", view_func=register_agent, methods=["POST"])
    except Exception as e:
        logging.warning(f"Konnte Alias-Routen nicht registrieren: {e}")




def _should_skip_threads_for_reloader() -> bool:
    return os.environ.get("WERKZEUG_RUN_MAIN") != "true" and os.environ.get("FLASK_DEBUG") == "1"


def _start_background_services(app: Flask) -> None:
    from agent.lifecycle import BackgroundServiceManager
    BackgroundServiceManager(app).start_all()


def create_app(agent: str = "default") -> Flask:
    """Erzeugt die Flask-App fuer den Agenten (API-Server)."""
    setup_logging(level=settings.log_level, json_format=settings.log_json)
    setup_signal_handlers()
    _log_runtime_hints()
    _configure_audit_logger()
    init_db()

    app = Flask(__name__)
    _register_request_hooks(app)
    register_error_handler(app)
    _configure_cors(app)
    app.config.update(build_base_app_config(agent))
    _configure_swagger(app)
    _register_blueprints(app)
    _register_alias_routes(app)
    _load_extensions(app)
    initialize_runtime_state(app)
    initialize_core_services(app)
    _start_background_services(app)
    return app


def _log_runtime_hints() -> None:
    """Logs actionable host/runtime hints for common local Docker issues."""
    if settings.redis_url:
        try:
            overcommit_path = "/proc/sys/vm/overcommit_memory"
            if os.path.exists(overcommit_path):
                with open(overcommit_path, "r", encoding="utf-8") as f:
                    overcommit = f.read().strip()
                if overcommit != "1":
                    msg = (
                        "Host kernel setting vm.overcommit_memory=%s detected. "
                        "Redis can become unstable under memory pressure. "
                        "Run setup_host_services.ps1 on Windows host."
                    )
                    # In containers this is usually controlled by the host/WSL runtime and
                    # cannot be changed from inside the app process.
                    if os.path.exists("/.dockerenv"):
                        logging.info(msg, overcommit)
                    else:
                        logging.warning(msg, overcommit)
        except Exception as e:
            logging.debug(f"Could not read vm.overcommit_memory: {e}")


def _load_extensions(app: Flask) -> None:
    for mod_name in [m.strip() for m in settings.extensions.split(",") if m.strip()]:
        try:
            module = __import__(mod_name, fromlist=["*"])
            if hasattr(module, "init_app"):
                module.init_app(app)
                logging.info(f"Extension geladen: {mod_name} (init_app)")
            elif hasattr(module, "bp"):
                app.register_blueprint(module.bp)
                logging.info(f"Extension geladen: {mod_name} (bp)")
            elif hasattr(module, "blueprint"):
                app.register_blueprint(module.blueprint)
                logging.info(f"Extension geladen: {mod_name} (blueprint)")
            else:
                logging.warning(f"Extension {mod_name} hat keine init_app/bp/blueprint")
        except Exception as e:
            logging.error(f"Fehler beim Laden der Extension {mod_name}: {e}")
    try:
        from agent.plugin_loader import load_plugins

        load_plugins(app)
    except Exception as e:
        logging.error(f"Fehler beim Laden der Plugins: {e}")




if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=settings.port)
