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
from agent.routes.config import config_bp
from agent.routes.knowledge import knowledge_bp
from agent.routes.openai_compat import openai_compat_bp
from agent.routes.sgpt import sgpt_bp
from agent.routes.system import system_bp
from agent.routes.tasks import register_tasks_blueprints, tasks_bp
from agent.routes.teams import teams_bp
from agent.utils import _archive_old_tasks, _archive_terminal_logs, _cleanup_old_backups, read_json, register_with_hub
from agent.ws_terminal import register_ws_terminal
from agent.config_defaults import build_default_agent_config, merge_db_config_overrides, sync_runtime_state
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




def _build_app_config(agent: str) -> dict:
    agent_name = settings.agent_name if settings.agent_name != "default" else agent
    return {
        "AGENT_NAME": agent_name,
        "AGENT_TOKEN": settings.agent_token,
        "PROVIDER_URLS": {
            "ollama": settings.ollama_url,
            "lmstudio": settings.lmstudio_url,
            "openai": settings.openai_url,
            "codex": settings.openai_url,
            "anthropic": settings.anthropic_url,
        },
        "OPENAI_API_KEY": settings.openai_api_key,
        "ANTHROPIC_API_KEY": settings.anthropic_api_key,
        "DATA_DIR": settings.data_dir,
        "TASKS_PATH": os.path.join(settings.data_dir, "tasks"),
        "AGENTS_PATH": os.path.join(settings.data_dir, "agents"),
    }


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
    app.register_blueprint(config_bp)
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
    app.config.update(_build_app_config(agent))
    _configure_swagger(app)
    _register_blueprints(app)
    _register_alias_routes(app)
    _load_extensions(app)

    from agent.routes.system import _load_history

    _load_history(app)

    default_cfg = build_default_agent_config()
    merge_db_config_overrides(default_cfg)
    app.config["AGENT_CONFIG"] = default_cfg
    sync_runtime_state(app, default_cfg)

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


def _check_token_rotation(app):
    """Prüft, ob der Token rotiert werden muss."""
    token_path = settings.token_path
    if not token_path or not os.path.exists(token_path):
        return

    try:
        token_data = read_json(token_path)
        last_rotation = token_data.get("last_rotation", 0)

        rotation_interval = settings.token_rotation_days * 86400
        if time.time() - last_rotation > rotation_interval:
            logging.info("Token-Rotations-Intervall erreicht. Starte Rotation...")
            with app.app_context():
                from agent.auth import rotate_token

                rotate_token()
    except (OperationalError, Exception) as e:
        is_db_err = isinstance(e, OperationalError) or "OperationalError" in str(e)
        if is_db_err:
            # Wir loggen hier nur auf DEBUG oder reichen es hoch,
            # da run_housekeeping sich um das konsolidierte Logging kümmert.
            # Aber falls es direkt aufgerufen wird, ist ein Info-Log sinnvoll.
            logging.info(f"Datenbank vorübergehend nicht erreichbar bei Token-Rotation: {e}")
        else:
            logging.error(f"Fehler bei der Prüfung der Token-Rotation: {e}")


def _is_db_operational_error(exc: Exception) -> bool:
    return isinstance(exc, OperationalError) or "OperationalError" in str(exc)


def _sleep_with_shutdown(total_seconds: int) -> None:
    import agent.common.context

    for _ in range(total_seconds):
        if agent.common.context.shutdown_requested:
            break
        time.sleep(1)


def _start_housekeeping_thread(app):
    def run_housekeeping():
        import agent.common.context

        logging.info("Housekeeping-Task gestartet.")
        consecutive_db_errors = 0
        while not agent.common.context.shutdown_requested:
            try:
                # Terminal-Logs archivieren
                _archive_terminal_logs()

                # Backups bereinigen
                _cleanup_old_backups()

                # Tasks archivieren
                _archive_old_tasks(app.config["TASKS_PATH"])

                # Token Rotation prüfen
                _check_token_rotation(app)
                consecutive_db_errors = 0
            except (OperationalError, Exception) as e:
                is_db_err = _is_db_operational_error(e)
                if is_db_err:
                    consecutive_db_errors += 1
                    if consecutive_db_errors <= 2:
                        logging.info(f"Datenbank vorübergehend nicht erreichbar (Housekeeping): {e}")
                    else:
                        # Housekeeping läuft alle 10 Min, daher ist jeder Fehler nach dem 2. (20 Min) eine Warnung wert
                        logging.warning(
                            "Datenbank weiterhin nicht erreichbar "
                            f"(Housekeeping, {consecutive_db_errors} Versuche): {e}"
                        )
                else:
                    logging.error(f"Fehler im Housekeeping-Task: {e}")

            # Alle 10 Minuten prüfen, aber auf Shutdown reagieren
            _sleep_with_shutdown(600)
        logging.info("Housekeeping-Task beendet.")

    t = threading.Thread(target=run_housekeeping, daemon=True)
    import agent.common.context

    agent.common.context.active_threads.append(t)
    t.start()


def _start_monitoring_thread(app):
    from agent.routes.system import check_all_agents_health, record_stats

    def should_run_monitoring() -> bool:
        return settings.role == "hub" or os.path.exists(app.config["AGENTS_PATH"])

    def log_monitoring_error(exc: Exception, db_error_count: int) -> int:
        if not _is_db_operational_error(exc):
            logging.error(f"Fehler im Monitoring-Task: {exc}")
            return db_error_count
        db_error_count += 1
        if db_error_count <= 3:
            logging.info(f"Datenbank vorübergehend nicht erreichbar (Monitoring): {exc}")
        elif db_error_count % 10 == 0:
            logging.warning(f"Datenbank weiterhin nicht erreichbar (Monitoring, {db_error_count} Versuche): {exc}")
        return db_error_count

    def run_monitoring():
        import agent.common.context

        if not should_run_monitoring():
            return

        logging.info("Status-Monitoring-Task gestartet.")
        consecutive_db_errors = 0
        while not agent.common.context.shutdown_requested:
            try:
                check_all_agents_health(app)
                record_stats(app)
                consecutive_db_errors = 0
            except (OperationalError, Exception) as e:
                consecutive_db_errors = log_monitoring_error(e, consecutive_db_errors)

            _sleep_with_shutdown(60)
        logging.info("Status-Monitoring-Task beendet.")

    t = threading.Thread(target=run_monitoring, daemon=True)
    import agent.common.context

    agent.common.context.active_threads.append(t)
    t.start()


def _start_registration_thread(app):
    def run_register():
        import agent.common.context

        register_as_worker = settings.role == "worker" or (settings.role == "hub" and settings.hub_can_be_worker)
        if not register_as_worker:
            return

        max_retries = 10
        base_delay = 2

        for i in range(max_retries):
            if agent.common.context.shutdown_requested:
                logging.info("Hub-Registrierung wegen Shutdown abgebrochen.")
                break

            # Die ersten 3 Versuche sind silent, um Log-Spam während des Container-Startups zu vermeiden
            silent = i < 3

            success = register_with_hub(
                hub_url=settings.hub_url,
                agent_name=(
                    app.config["AGENT_NAME"]
                    if settings.role == "worker"
                    else f"{app.config['AGENT_NAME']}-local-worker"
                ),
                port=settings.port,
                token=app.config["AGENT_TOKEN"],
                role="worker",
                silent=silent,
            )

            if success:
                return

            delay = min(base_delay * (2**i), 300)  # Max 5 Minuten
            if not silent:
                logging.warning(f"Hub-Registrierung fehlgeschlagen. Retry {i + 1}/{max_retries} in {delay}s...")
            else:
                logging.info(f"Hub noch nicht bereit, erneuter Versuch in {delay}s... (Versuch {i + 1})")

            # Schlafen in kleinen Schritten, um auf Shutdown reagieren zu können
            for _ in range(delay):
                if agent.common.context.shutdown_requested:
                    break
                time.sleep(1)

    t = threading.Thread(target=run_register, daemon=True)
    import agent.common.context

    agent.common.context.active_threads.append(t)
    t.start()


def _get_llm_target(app) -> tuple[str, str] | None:
    provider = str((app.config.get("AGENT_CONFIG", {}) or {}).get("default_provider") or settings.default_provider or "")
    url = app.config["PROVIDER_URLS"].get(provider)
    if not url or provider in ["openai", "anthropic"]:
        logging.info(f"LLM-Check fuer {provider} uebersprungen (Cloud-Provider oder keine URL).")
        return None
    return provider, url


def _handle_llm_probe_result(provider: str, url: str, latency: float, is_ok: bool, last_state_ok: bool | None) -> bool:
    if is_ok:
        if latency > 2.0:
            logging.warning(f"LLM-Latenz-Warnung: {provider} antwortet langsam ({latency:.2f}s).")
            from agent.llm_integration import _report_llm_failure

            _report_llm_failure(provider)
        if last_state_ok is not True:
            logging.info(f"LLM-Verbindung zu {provider} ist ERREICHBAR. (Latenz: {latency:.2f}s)")
        return True
    if last_state_ok is not False:
        logging.warning(f"!!! LLM-WARNUNG !!!: {provider} unter {url} ist aktuell NICHT ERREICHBAR.")
        logging.warning("Tipp: Fuehren Sie 'setup_host_services.ps1' auf Ihrem Windows-Host aus.")
    return False


def _run_llm_check_loop(app) -> None:
    import agent.common.context
    from agent.common.http import HttpClient

    time.sleep(5)
    target = _get_llm_target(app)
    if target is None:
        return
    provider, url = target
    check_client = HttpClient(timeout=5, retries=0)
    logging.info(f"LLM-Monitoring fuer {provider} ({url}) gestartet.")
    last_state_ok = None

    while not agent.common.context.shutdown_requested:
        try:
            start_time = time.time()
            res = check_client.get(url, timeout=5, silent=True, return_response=True)
            latency = time.time() - start_time
            is_ok = res is not None and res.status_code < 500
            last_state_ok = _handle_llm_probe_result(provider, url, latency, is_ok, last_state_ok)
        except Exception as e:
            if last_state_ok is not False:
                logging.warning(f"Fehler beim Test der LLM-Verbindung: {e}")
            last_state_ok = False
        _sleep_with_shutdown(300)

    logging.info("LLM-Monitoring-Task beendet.")


def _start_llm_check_thread(app):
    """Prueft periodisch die Erreichbarkeit des konfigurierten LLM-Providers."""
    t = threading.Thread(target=lambda: _run_llm_check_loop(app), daemon=True)
    import agent.common.context

    agent.common.context.active_threads.append(t)
    t.start()


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=settings.port)
