import logging
import json
from flask import Blueprint, current_app, request
from agent.auth import check_auth, admin_required
from agent.common.errors import api_response
from agent.config import settings
from agent.repository import config_repo
from agent.config_defaults import sync_runtime_state

core_bp = Blueprint("config_core", __name__)

@core_bp.route("/config", methods=["GET"])
def get_config():
    check_auth()
    return api_response(data=current_app.config.get("AGENT_CONFIG", {}))

@core_bp.route("/config", methods=["POST"])
def update_config():
    admin_required()
    data = request.get_json(silent=True) or {}
    if not data:
        return api_response(status="error", message="Keine Daten empfangen", code=400)

    # In DB speichern
    for key, value in data.items():
        config_repo.upsert(key, json.dumps(value))

    # Laufzeit-Zustand aktualisieren
    agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    agent_cfg.update(data)
    current_app.config["AGENT_CONFIG"] = agent_cfg
    sync_runtime_state(current_app, agent_cfg, changed_keys=set(data.keys()))

    return api_response(message="Konfiguration aktualisiert")

@core_bp.route("/llm/models", methods=["GET"])
def list_llm_models():
    check_auth()
    # Hier wuerde normalerweise die Logik zur Abfrage der Provider stehen
    # Fuers Erste geben wir die konfigurierten Modelle zurueck
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    return api_response(data={
        "default_model": cfg.get("default_model"),
        "default_provider": cfg.get("default_provider"),
        "providers": list(current_app.config.get("PROVIDER_URLS", {}).keys())
    })
