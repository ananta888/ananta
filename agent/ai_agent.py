import logging
import os
import time

from flask import Flask

from agent.bootstrap.background import start_background_services
from agent.bootstrap.extensions import configure_cors, configure_swagger, load_extensions
from agent.bootstrap.request_hooks import configure_audit_logger, register_request_hooks
from agent.bootstrap.routes import register_alias_routes, register_blueprints
from agent.bootstrap.runtime_hints import log_runtime_hints
from agent.common.error_handler import register_error_handler
from agent.common.logging import setup_logging
from agent.common.signals import setup_signal_handlers
from agent.config import settings
from agent.database import init_db
from agent.metrics import APP_STARTUP_DURATION
from agent.services.app_runtime_service import build_base_app_config, initialize_runtime_state
from agent.services.repository_registry import initialize_repository_registry
from agent.services.service_registry import initialize_core_services
from agent.utils import read_json
from agent.utils import register_with_hub as _register_with_hub

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


def _check_token_rotation(app: Flask) -> None:
    """Backward-compatible token-rotation check used by legacy tests."""
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


def create_app(agent: str = "default") -> Flask:
    """Erzeugt die Flask-App fuer den Agenten (API-Server)."""
    _start_perf = time.perf_counter()
    setup_logging(level=settings.log_level, json_format=settings.log_json)
    setup_signal_handlers()
    log_runtime_hints()
    configure_audit_logger()
    init_db()

    app = Flask(__name__)
    register_request_hooks(app)
    register_error_handler(app)
    configure_cors(app)
    app.config.update(build_base_app_config(agent))
    configure_swagger(app)
    register_blueprints(app)
    register_alias_routes(app)
    initialize_runtime_state(app)
    load_extensions(app)
    initialize_repository_registry(app)
    initialize_core_services(app)
    start_background_services(app)

    elapsed = time.perf_counter() - _start_perf
    APP_STARTUP_DURATION.set(elapsed)
    logging.info(f"Ananta Agent '{agent}' (role={settings.role}) started in {elapsed:.4f}s")

    return app


if __name__ == "__main__":
    import sys

    if "--version" in sys.argv:
        from agent.config import settings

        print(f"Ananta Agent v{settings.version}")
        sys.exit(0)

    app = create_app()
    app.run(host="0.0.0.0", port=settings.port)
