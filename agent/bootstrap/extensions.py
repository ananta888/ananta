import logging

from flask import Flask

try:
    from flask_cors import CORS
except ImportError:
    CORS = None

try:
    from flasgger import Swagger
except ImportError:
    Swagger = None

from agent.config import settings


def configure_cors(app: Flask) -> None:
    if not CORS:
        return
    try:
        origins = settings.cors_origins
        if "," in origins:
            origins = [o.strip() for o in origins.split(",")]
        CORS(app, resources={r"*": {"origins": origins}})
    except Exception as e:
        logging.error(f"CORS konnte nicht initialisiert werden: {e}")


def configure_swagger(app: Flask) -> None:
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


def load_extensions(app: Flask) -> None:
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

