import os
import uuid
import logging
import threading
import signal
import time
from flask import Flask, jsonify, request

try:
    from flask_cors import CORS
except ImportError:
    CORS = None

try:
    from flasgger import Swagger
except ImportError:
    Swagger = None

from agent.config import settings
from agent.common.logging import setup_logging, set_correlation_id, get_correlation_id
from agent.common.errors import (
    AnantaError, TransientError, PermanentError, ValidationError as AnantaValidationError
)
from agent.routes.system import system_bp
from agent.routes.config import config_bp
from agent.routes.tasks import tasks_bp
from agent.utils import _http_post, read_json, register_with_hub, _archive_terminal_logs, _archive_old_tasks
from agent.shell import get_shell

# Konstanten
_shutdown_requested = False

def _handle_shutdown(signum, frame):
    global _shutdown_requested
    logging.info("Shutdown Signal empfangen...")
    _shutdown_requested = True
    try:
        get_shell().close()
    except Exception as e:
        logging.error(f"Fehler beim Schließen der Shell: {e}")
    
    try:
        from agent.scheduler import get_scheduler
        get_scheduler().stop()
    except Exception as e:
        logging.error(f"Fehler beim Stoppen des Schedulers: {e}")

signal.signal(signal.SIGTERM, _handle_shutdown)
signal.signal(signal.SIGINT, _handle_shutdown)

def create_app(agent: str = "default") -> Flask:
    """Erzeugt die Flask-App für den Agenten (API-Server)."""
    setup_logging(level=settings.log_level, json_format=settings.log_json)
    app = Flask(__name__)

    @app.before_request
    def ensure_correlation_id_and_check_shutdown():
        cid = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        set_correlation_id(cid)
        if _shutdown_requested and request.endpoint not in ('system.health', 'tasks.get_logs', 'tasks.task_logs'):
            return jsonify({"status": "shutdown_in_progress"}), 503

    @app.errorhandler(Exception)
    def handle_exception(e):
        cid = get_correlation_id()
        if isinstance(e, AnantaError):
            logging.warning(f"{e.__class__.__name__} [CID: {cid}]: {e}")
        else:
            logging.exception(f"Unbehandelte Exception [CID: {cid}]: {e}")

        if isinstance(e, AnantaValidationError):
            return jsonify({"error": "validation_failed", "details": e.details, "cid": cid}), 422
        
        if isinstance(e, PermanentError):
            return jsonify({"error": "permanent_error", "message": str(e), "cid": cid}), 400
            
        if isinstance(e, TransientError):
            return jsonify({"error": "transient_error", "message": str(e), "cid": cid}), 503

        code = 500
        if hasattr(e, "code"): code = getattr(e, "code")
        return jsonify({
            "error": "internal_server_error",
            "message": str(e) if code != 500 else "Ein interner Fehler ist aufgetreten.",
            "cid": cid
        }), code

    if CORS:
        try:
            origins = settings.cors_origins
            if "," in origins:
                origins = [o.strip() for o in origins.split(",")]
            CORS(app, resources={r"*": {"origins": origins}})
        except Exception as e:
            logging.error(f"CORS konnte nicht initialisiert werden: {e}")

    # Persistierten Token laden falls vorhanden
    token_path = os.path.join(settings.data_dir, "token.json")
    persisted_token = None
    if os.path.exists(token_path):
        try:
            token_data = read_json(token_path)
            persisted_token = token_data.get("agent_token")
            if persisted_token:
                logging.info(f"Persistierter Agent Token aus {token_path} geladen.")
        except Exception as e:
            logging.error(f"Fehler beim Laden des persistierten Tokens: {e}")

    # App Config
    agent_name = settings.agent_name if settings.agent_name != "default" else agent
    app.config.update({
        "AGENT_NAME": agent_name,
        "AGENT_TOKEN": persisted_token or settings.agent_token,
        "PROVIDER_URLS": {
            "ollama": settings.ollama_url,
            "lmstudio": settings.lmstudio_url,
            "openai": settings.openai_url,
            "anthropic": settings.anthropic_url,
        },
        "OPENAI_API_KEY": settings.openai_api_key,
        "ANTHROPIC_API_KEY": settings.anthropic_api_key,
        "DATA_DIR": settings.data_dir,
        "CONFIG_PATH": os.path.join(settings.data_dir, "config.json"),
        "TEMPLATES_PATH": os.path.join(settings.data_dir, "templates.json"),
        "TASKS_PATH": os.path.join(settings.data_dir, "tasks.json"),
        "AGENTS_PATH": os.path.join(settings.data_dir, "agents.json"),
        "TOKEN_PATH": os.path.join(settings.data_dir, "token.json"),
        "STATS_HISTORY_PATH": os.path.join(settings.data_dir, "stats_history.json"),
    })

    # Swagger-Dokumentation initialisieren
    if Swagger:
        Swagger(app, template={
            "swagger": "2.0",
            "info": {
                "title": "Ananta Agent API",
                "description": "API Dokumentation für den Ananta Agenten",
                "version": "1.0.0"
            },
            "securityDefinitions": {
                "Bearer": {
                    "type": "apiKey",
                    "name": "Authorization",
                    "in": "header",
                    "description": "JWT Token im Format 'Bearer <token>'"
                }
            },
            "security": [
                {
                    "Bearer": []
                }
            ]
        })

    # Blueprints registrieren
    app.register_blueprint(system_bp)
    app.register_blueprint(config_bp)
    app.register_blueprint(tasks_bp)

    # Historie laden
    from agent.routes.system import _load_history
    _load_history(app)

    # Initial Agent Config laden
    default_cfg = {"provider": "ollama", "model": "llama3", "max_summary_length": 500}
    saved_cfg = read_json(app.config["CONFIG_PATH"], {})
    default_cfg.update(saved_cfg)
    app.config["AGENT_CONFIG"] = default_cfg

    # Registrierung am Hub
    _start_registration_thread(app)

    # Monitoring-Thread starten (nur für Hub)
    if not app.testing:
        _start_monitoring_thread(app)

    # Housekeeping-Thread starten (für alle Rollen)
    if not app.testing:
        _start_housekeeping_thread(app)

    # Scheduler starten
    if not app.testing:
        from agent.scheduler import get_scheduler
        get_scheduler().start()

    return app

def _check_token_rotation(app):
    """Prüft, ob der Token rotiert werden muss."""
    token_path = app.config.get("TOKEN_PATH")
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
    except Exception as e:
        logging.error(f"Fehler bei der Prüfung der Token-Rotation: {e}")

def _start_housekeeping_thread(app):
    def run_housekeeping():
        logging.info("Housekeeping-Task gestartet.")
        while not _shutdown_requested:
            try:
                # Terminal-Logs archivieren
                _archive_terminal_logs()
                
                # Tasks archivieren
                _archive_old_tasks(app.config["TASKS_PATH"])

                # Token Rotation prüfen
                _check_token_rotation(app)
            except Exception as e:
                logging.error(f"Fehler im Housekeeping-Task: {e}")
            
            # Alle 10 Minuten prüfen, aber auf Shutdown reagieren
            for _ in range(600):
                if _shutdown_requested: break
                time.sleep(1)
        logging.info("Housekeeping-Task beendet.")

    threading.Thread(target=run_housekeeping, daemon=True).start()

def _start_monitoring_thread(app):
    from agent.routes.system import check_all_agents_health, record_stats
    
    def run_monitoring():
        if settings.role != "hub": 
            # Falls wir nicht explizit Hub sind, aber AGENTS_PATH haben, 
            # könnten wir trotzdem monitoren, aber laut Anforderung ist es für den Hub.
            # Wir prüfen ob wir Agenten zum Verwalten haben.
            if not os.path.exists(app.config["AGENTS_PATH"]):
                return

        logging.info("Status-Monitoring-Task gestartet.")
        while not _shutdown_requested:
            try:
                check_all_agents_health(app)
                record_stats(app)
            except Exception as e:
                logging.error(f"Fehler im Monitoring-Task: {e}")
            
            # 60 Sekunden warten, aber auf Shutdown reagieren
            for _ in range(60):
                if _shutdown_requested: break
                time.sleep(1)
        logging.info("Status-Monitoring-Task beendet.")

    threading.Thread(target=run_monitoring, daemon=True).start()

def _start_registration_thread(app):
    def run_register():
        if settings.role != "worker": return
        
        max_retries = 10
        base_delay = 2
        
        for i in range(max_retries):
            if _shutdown_requested:
                logging.info("Hub-Registrierung wegen Shutdown abgebrochen.")
                break
                
            success = register_with_hub(
                hub_url=settings.hub_url,
                agent_name=app.config["AGENT_NAME"],
                port=settings.port,
                token=app.config["AGENT_TOKEN"],
                role=settings.role
            )
            
            if success:
                return
            
            delay = min(base_delay * (2 ** i), 300) # Max 5 Minuten
            logging.warning(f"Hub-Registrierung fehlgeschlagen. Retry {i+1}/{max_retries} in {delay}s...")
            
            # Schlafen in kleinen Schritten, um auf Shutdown reagieren zu können
            for _ in range(delay):
                if _shutdown_requested: break
                time.sleep(1)
    
    threading.Thread(target=run_register, daemon=True).start()

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=settings.port)