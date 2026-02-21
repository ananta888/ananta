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
from agent.routes.auth import auth_bp
from agent.routes.config import config_bp
from agent.routes.sgpt import sgpt_bp
from agent.routes.system import system_bp
from agent.routes.tasks import register_tasks_blueprints, tasks_bp
from agent.routes.teams import teams_bp
from agent.utils import _archive_old_tasks, _archive_terminal_logs, _cleanup_old_backups, read_json, register_with_hub
from agent.ws_terminal import register_ws_terminal


def _is_truthy_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _background_threads_disabled(app: Flask) -> bool:
    return bool(
        app.testing
        or os.environ.get("PYTEST_CURRENT_TEST")
        or _is_truthy_env(os.environ.get("ANANTA_DISABLE_BACKGROUND_THREADS"))
    )


def create_app(agent: str = "default") -> Flask:
    """Erzeugt die Flask-App für den Agenten (API-Server)."""
    setup_logging(level=settings.log_level, json_format=settings.log_json)
    setup_signal_handlers()
    _log_runtime_hints()

    # Audit Logging konfigurieren
    audit_file = os.path.join(settings.data_dir, "audit.log")
    audit_handler = logging.FileHandler(audit_file, encoding="utf-8")
    if settings.log_json:
        audit_handler.setFormatter(JsonFormatter())
    else:
        audit_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))

    audit_logger = logging.getLogger("audit")
    audit_logger.setLevel(logging.INFO)
    audit_logger.addHandler(audit_handler)
    audit_logger.propagate = False  # Nicht an Root-Logger weitergeben

    # DB initialisieren
    init_db()

    app = Flask(__name__)

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
        """Fügt Standard-Security-Header zu jeder Response hinzu."""
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")

        # Content Security Policy (CSP)
        # Standardmäßig strikt ohne 'unsafe-inline'. Für Swagger gibt es eine dedizierte,
        # kompatible Policy, damit die Doku weiterhin nutzbar bleibt.
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

        # HSTS (Strict-Transport-Security)
        # Wir prüfen auch auf X-Forwarded-Proto, falls die App hinter einem Proxy (Nginx/Traefik) läuft.
        is_https = request.is_secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https"
        if is_https:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains; preload")

        return response

    @app.errorhandler(Exception)
    def handle_exception(e):
        cid = get_correlation_id()
        if isinstance(e, HTTPException):
            code = getattr(e, "code", 500) or 500
            if code == 404:
                logging.info(f"Erwarteter HTTP-Fehler {code} [CID: {cid}]: {e}")
            elif code < 500:
                logging.warning(f"HTTP-Fehler {code} [CID: {cid}]: {e}")
            else:
                logging.exception(f"HTTP-Serverfehler {code} [CID: {cid}]: {e}")
        elif isinstance(e, AnantaError):
            logging.warning(f"{e.__class__.__name__} [CID: {cid}]: {e}")
        else:
            logging.exception(f"Unbehandelte Exception [CID: {cid}]: {e}")

        if isinstance(e, AnantaValidationError):
            return api_response(
                status="error", message="validation_failed", data={"details": e.details, "cid": cid}, code=422
            )

        if isinstance(e, PermanentError):
            return api_response(status="error", message=str(e), data={"cid": cid}, code=400)

        if isinstance(e, TransientError):
            return api_response(status="error", message=str(e), data={"cid": cid}, code=503)

        code = 500
        if hasattr(e, "code"):
            code = getattr(e, "code")
        msg = str(e) if code != 500 else "Ein interner Fehler ist aufgetreten."
        return api_response(status="error", message=msg, data={"cid": cid}, code=code)

    if CORS:
        try:
            origins = settings.cors_origins
            if "," in origins:
                origins = [o.strip() for o in origins.split(",")]
            CORS(app, resources={r"*": {"origins": origins}})
        except Exception as e:
            logging.error(f"CORS konnte nicht initialisiert werden: {e}")

    # App Config
    agent_name = settings.agent_name if settings.agent_name != "default" else agent
    app.config.update(
        {
            "AGENT_NAME": agent_name,
            "AGENT_TOKEN": settings.agent_token,
            "PROVIDER_URLS": {
                "ollama": settings.ollama_url,
                "lmstudio": settings.lmstudio_url,
                "openai": settings.openai_url,
                "anthropic": settings.anthropic_url,
            },
            "OPENAI_API_KEY": settings.openai_api_key,
            "ANTHROPIC_API_KEY": settings.anthropic_api_key,
            "DATA_DIR": settings.data_dir,
            "TASKS_PATH": os.path.join(settings.data_dir, "tasks"),
            "AGENTS_PATH": os.path.join(settings.data_dir, "agents"),
        }
    )

    # Swagger-Dokumentation initialisieren
    if Swagger:
        Swagger(
            app,
            template={
                "swagger": "2.0",
                "info": {
                    "title": "Ananta Agent API",
                    "description": "API Dokumentation für den Ananta Agenten",
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

    # Blueprints registrieren
    app.register_blueprint(system_bp, url_prefix="/api/system")
    app.register_blueprint(config_bp)
    app.register_blueprint(tasks_bp)
    register_tasks_blueprints(app)
    app.register_blueprint(teams_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(sgpt_bp, url_prefix="/api/sgpt")
    register_ws_terminal(app)

    # Alias-Routen ohne Präfix für Tests/Kompatibilität
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

    _load_extensions(app)

    # Historie laden
    from agent.routes.system import _load_history

    _load_history(app)

    # Initial Agent Config laden
    default_cfg = {
        "default_provider": settings.default_provider,
        "default_model": settings.default_model,
        "provider": settings.default_provider,
        "model": settings.default_model,
        "llm_config": {
            "provider": settings.default_provider,
            "model": settings.default_model,
            "base_url": settings.lmstudio_url if settings.default_provider == "lmstudio" else None,
            "lmstudio_api_mode": settings.lmstudio_api_mode,
        },
        "max_summary_length": 500,
        "quality_gates": {
            "enabled": True,
            "autopilot_enforce": True,
            "coding_keywords": [
                "code",
                "implement",
                "fix",
                "refactor",
                "bug",
                "test",
                "feature",
                "endpoint",
            ],
            "required_output_markers_for_coding": [
                "test",
                "pytest",
                "passed",
                "success",
                "lint",
                "ok",
            ],
            "min_output_chars": 8,
        },
        "autonomous_guardrails": {
            "enabled": True,
            "max_runtime_seconds": 21600,
            "max_ticks_total": 5000,
            "max_dispatched_total": 50000,
        },
        "llm_tool_guardrails": {
            "enabled": True,
            "max_tool_calls_per_request": 5,
            "max_external_calls_per_request": 2,
            "max_estimated_cost_units_per_request": 20,
            "max_tokens_per_request": 6000,
            "chars_per_token_estimate": 4,
            "class_limits": {"read": 5, "write": 2, "admin": 1},
            "class_cost_units": {"read": 1, "write": 5, "admin": 8, "unknown": 3},
            "external_classes": ["write", "admin"],
            "tool_classes": {
                "list_teams": "read",
                "list_roles": "read",
                "list_agents": "read",
                "list_templates": "read",
                "analyze_logs": "read",
                "read_agent_logs": "read",
                "create_team": "write",
                "assign_role": "write",
                "ensure_team_templates": "write",
                "create_template": "write",
                "update_template": "write",
                "delete_template": "write",
                "update_config": "admin",
            },
        },
        "autonomous_resilience": {
            "retry_attempts": 2,
            "retry_backoff_seconds": 0.2,
            "circuit_breaker_threshold": 3,
            "circuit_breaker_open_seconds": 30,
        },
        "autopilot_security_policies": {
            "safe": {
                "max_concurrency_cap": 1,
                "execute_timeout": 45,
                "execute_retries": 0,
                "allowed_tool_classes": ["read"],
            },
            "balanced": {
                "max_concurrency_cap": 2,
                "execute_timeout": 60,
                "execute_retries": 1,
                "allowed_tool_classes": ["read", "write"],
            },
            "aggressive": {
                "max_concurrency_cap": 4,
                "execute_timeout": 120,
                "execute_retries": 2,
                "allowed_tool_classes": ["read", "write", "admin", "unknown"],
            },
        },
        "sgpt_routing": {
            "policy_version": "v2",
            "default_backend": "sgpt",
            "task_kind_backend": {
                "coding": "aider",
                "analysis": "sgpt",
                "doc": "sgpt",
                "ops": "opencode",
            },
        },
    }

    # Aus DB laden falls vorhanden
    try:
        from agent.repository import config_repo

        db_configs = config_repo.get_all()
        import json

        reserved_keys = {"status", "message", "code"}  # 'data' ist grenzwertig, aber oft Key
        for cfg in db_configs:
            if cfg.key in reserved_keys:
                continue
            try:
                val = json.loads(cfg.value_json)
                # Tiefenprüfung auf Verschachtelung (Fix für bestehende korrupte Daten)
                from agent.routes.config import unwrap_config

                val = unwrap_config(val)
                default_cfg[cfg.key] = val
            except Exception:
                default_cfg[cfg.key] = cfg.value_json
    except Exception as e:
        logging.warning(f"Konnte Konfiguration nicht aus DB laden: {e}. Nutze Fallback.")

    app.config["AGENT_CONFIG"] = default_cfg

    # LLM Config synchronisieren
    if "llm_config" in default_cfg:
        lc = default_cfg["llm_config"]
        prov = lc.get("provider")

        # Provider und Model synchronisieren
        if prov and hasattr(settings, "default_provider"):
            setattr(settings, "default_provider", prov)
        if lc.get("model") and hasattr(settings, "default_model"):
            setattr(settings, "default_model", lc.get("model"))
        if lc.get("lmstudio_api_mode") and hasattr(settings, "lmstudio_api_mode"):
            setattr(settings, "lmstudio_api_mode", lc.get("lmstudio_api_mode"))

        if prov:
            if lc.get("base_url"):
                app.config["PROVIDER_URLS"][prov] = lc.get("base_url")
                # Auch in settings synchronisieren
                url_attr = f"{prov}_url"
                if hasattr(settings, url_attr):
                    setattr(settings, url_attr, lc.get("base_url"))
            if lc.get("api_key"):
                key_attr = f"{prov}_api_key"
                if hasattr(settings, key_attr):
                    setattr(settings, key_attr, lc.get("api_key"))
                if prov == "openai":
                    app.config["OPENAI_API_KEY"] = lc.get("api_key")
                elif prov == "anthropic":
                    app.config["ANTHROPIC_API_KEY"] = lc.get("api_key")

            # Provider und Modell als Defaults in settings setzen
            if prov and hasattr(settings, "default_provider"):
                settings.default_provider = prov
            if lc.get("model") and hasattr(settings, "default_model"):
                settings.default_model = lc.get("model")
            if lc.get("lmstudio_api_mode") and hasattr(settings, "lmstudio_api_mode"):
                settings.lmstudio_api_mode = lc.get("lmstudio_api_mode")

    background_threads_disabled = _background_threads_disabled(app)

    # Threads nur im Hauptprozess starten (nicht im Flask-Reloader Child)
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true" and os.environ.get("FLASK_DEBUG") == "1":
        # Wir sind im Parent-Prozess des Reloaders, hier keine Threads starten
        # Wir entfernen auch die Signal-Handler, damit sie nur im Child laufen
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        pass
    else:
        if not background_threads_disabled:
            # Registrierung am Hub
            _start_registration_thread(app)

            # LLM-Erreichbarkeit prüfen
            if not settings.disable_llm_check:
                _start_llm_check_thread(app)

            # Monitoring-Thread starten (nur für Hub)
            _start_monitoring_thread(app)

            # Housekeeping-Thread starten (für alle Rollen)
            _start_housekeeping_thread(app)

            # Scheduler starten
            from agent.scheduler import get_scheduler

            get_scheduler().start()
        else:
            logging.info(
                "Background threads disabled (app.testing/PYTEST_CURRENT_TEST/ANANTA_DISABLE_BACKGROUND_THREADS)."
            )

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
            logging.warning(
                "Datenbank weiterhin nicht erreichbar "
                f"(Monitoring, {db_error_count} Versuche): {exc}"
            )
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
                    else f'{app.config["AGENT_NAME"]}-local-worker'
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
    provider = settings.default_provider
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
