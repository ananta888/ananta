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
from agent.common.logging import setup_logging, set_correlation_id, get_correlation_id, JsonFormatter
from agent.database import init_db
from agent.common.errors import (
    AnantaError, TransientError, PermanentError, ValidationError as AnantaValidationError
)
from agent.routes.system import system_bp
from agent.routes.config import config_bp
from agent.routes.tasks import tasks_bp
from agent.routes.teams import teams_bp
from agent.routes.auth import auth_bp
from agent.routes.sgpt import sgpt_bp
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
    
    # Audit Logging konfigurieren
    audit_file = os.path.join(settings.data_dir, "audit.log")
    audit_handler = logging.FileHandler(audit_file, encoding="utf-8")
    if settings.log_json:
        audit_handler.setFormatter(JsonFormatter())
    else:
        audit_handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s'))
    
    audit_logger = logging.getLogger("audit")
    audit_logger.setLevel(logging.INFO)
    audit_logger.addHandler(audit_handler)
    audit_logger.propagate = False # Nicht an Root-Logger weitergeben
    
    # DB initialisieren
    init_db()
    
    app = Flask(__name__)

    @app.before_request
    def ensure_correlation_id_and_check_shutdown():
        cid = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        set_correlation_id(cid)
        if _shutdown_requested and request.endpoint not in ('system.health', 'tasks.get_logs', 'tasks.task_logs'):
            return jsonify({"status": "shutdown_in_progress"}), 503

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        if request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

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
        "TOKEN_PATH": os.path.join(settings.data_dir, "token.json"),
        "TASKS_PATH": os.path.join(settings.data_dir, "tasks"),
        "AGENTS_PATH": os.path.join(settings.data_dir, "agents"),
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
    app.register_blueprint(teams_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(sgpt_bp)

    _load_extensions(app)

    # Historie laden
    from agent.routes.system import _load_history
    _load_history(app)

    # Initial Agent Config laden
    default_cfg = {"provider": "ollama", "model": "llama3", "max_summary_length": 500}
    
    # Aus DB laden falls vorhanden
    try:
        from agent.repository import config_repo
        db_configs = config_repo.get_all()
        import json
        for cfg in db_configs:
            try:
                default_cfg[cfg.key] = json.loads(cfg.value_json)
            except Exception:
                default_cfg[cfg.key] = cfg.value_json
    except Exception as e:
        logging.warning(f"Konnte Konfiguration nicht aus DB laden: {e}. Nutze Fallback.")

    app.config["AGENT_CONFIG"] = default_cfg

    # LLM Config synchronisieren
    if "llm_config" in default_cfg:
        lc = default_cfg["llm_config"]
        prov = lc.get("provider")
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

    # Registrierung am Hub
    _start_registration_thread(app)

    # LLM-Erreichbarkeit prüfen
    if not settings.disable_llm_check:
        _start_llm_check_thread(app)

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

def _load_extensions(app: Flask) -> None:
    if not settings.extensions:
        return
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

def _start_llm_check_thread(app):
    """Prüft periodisch die Erreichbarkeit des konfigurierten LLM-Providers."""
    def run_check():
        # Kurz warten, bis der Server hochgefahren ist
        time.sleep(5)
        
        provider = settings.default_provider
        url = app.config["PROVIDER_URLS"].get(provider)
        
        if not url or provider in ["openai", "anthropic"]:
            logging.info(f"LLM-Check für {provider} übersprungen (Cloud-Provider oder keine URL).")
            return

        from agent.common.http import HttpClient
        # Wir nutzen einen eigenen Client ohne Retries für den Check, um Log-Spam zu vermeiden
        check_client = HttpClient(timeout=5, retries=0)
        
        logging.info(f"LLM-Monitoring für {provider} ({url}) gestartet.")
        
        last_state_ok = None
        
        while not _shutdown_requested:
            try:
                # Wir nutzen einen kurzen Timeout für den Check
                res = check_client.get(url, timeout=5, silent=True, return_response=True)
                is_ok = res is not None and res.status_code < 500
                
                if is_ok:
                    if last_state_ok is not True:
                        logging.info(f"LLM-Verbindung zu {provider} ist ERREICHBAR.")
                    last_state_ok = True
                else:
                    if last_state_ok is not False:
                        logging.warning(f"!!! LLM-WARNUNG !!!: {provider} unter {url} ist aktuell NICHT ERREICHBAR.")
                        logging.warning("Tipp: Führen Sie 'setup_host_services.ps1' auf Ihrem Windows-Host aus.")
                    last_state_ok = False
            except Exception as e:
                if last_state_ok is not False:
                    logging.warning(f"Fehler beim Test der LLM-Verbindung: {e}")
                last_state_ok = False
            
            # Alle 5 Minuten prüfen, aber auf Shutdown reagieren
            for _ in range(300):
                if _shutdown_requested: break
                time.sleep(1)
                
        logging.info("LLM-Monitoring-Task beendet.")

    threading.Thread(target=run_check, daemon=True).start()

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=settings.port)
