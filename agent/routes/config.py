import uuid
from flask import Blueprint, jsonify, current_app, request, g
from agent.utils import validate_request, read_json, write_json
from agent.auth import check_auth
from agent.llm_integration import generate_text

config_bp = Blueprint("config", __name__)

@config_bp.route("/config", methods=["GET"])
@check_auth
def get_config():
    """
    Aktuelle Konfiguration abrufen
    ---
    security:
      - Bearer: []
    responses:
      200:
        description: Aktuelle Agenten-Konfiguration
    """
    return jsonify(current_app.config.get("AGENT_CONFIG", {}))

@config_bp.route("/config", methods=["POST"])
@check_auth
def set_config():
    """
    Konfiguration aktualisieren
    ---
    security:
      - Bearer: []
    responses:
      200:
        description: Konfiguration erfolgreich aktualisiert
    """
    new_cfg = request.get_json()
    if not isinstance(new_cfg, dict):
        return jsonify({"error": "invalid_json"}), 400
    
    current_cfg = current_app.config.get("AGENT_CONFIG", {})
    current_cfg.update(new_cfg)
    current_app.config["AGENT_CONFIG"] = current_cfg
    
    # LLM Config in App-Context synchronisieren falls vorhanden
    if "llm_config" in current_cfg:
        lc = current_cfg["llm_config"]
        prov = lc.get("provider")
        if prov and lc.get("base_url"):
            urls = current_app.config.get("PROVIDER_URLS", {}).copy()
            urls[prov] = lc.get("base_url")
            current_app.config["PROVIDER_URLS"] = urls
        if lc.get("api_key"):
            if prov == "openai":
                current_app.config["OPENAI_API_KEY"] = lc.get("api_key")
            elif prov == "anthropic":
                current_app.config["ANTHROPIC_API_KEY"] = lc.get("api_key")

    # Synchronisiere mit globaler settings-Instanz
    from agent.config import settings
    for key, value in new_cfg.items():
        if hasattr(settings, key):
            try:
                setattr(settings, key, value)
            except Exception as e:
                current_app.logger.warning(f"Konnte settings.{key} nicht aktualisieren: {e}")
    
    # Persistieren
    write_json(current_app.config["CONFIG_PATH"], current_cfg)
    
    return jsonify({"status": "updated", "config": current_cfg})

@config_bp.route("/templates", methods=["GET"])
@check_auth
def list_templates():
    tpls = read_json(current_app.config["TEMPLATES_PATH"], [])
    return jsonify(tpls)

@config_bp.route("/templates", methods=["POST"])
@check_auth
def create_template():
    data = request.get_json()
    tpls = read_json(current_app.config["TEMPLATES_PATH"], [])
    new_id = str(uuid.uuid4())
    data["id"] = new_id
    tpls.append(data)
    write_json(current_app.config["TEMPLATES_PATH"], tpls)
    return jsonify(data), 201

@config_bp.route("/templates/<tpl_id>", methods=["PATCH"])
@check_auth
def update_template(tpl_id):
    data = request.get_json()
    tpls = read_json(current_app.config["TEMPLATES_PATH"], [])
    for t in tpls:
        if t.get("id") == tpl_id:
            t.update(data)
            write_json(current_app.config["TEMPLATES_PATH"], tpls)
            return jsonify(t)
    return jsonify({"error": "not_found"}), 404

@config_bp.route("/templates/<tpl_id>", methods=["DELETE"])
@check_auth
def delete_template(tpl_id):
    tpls = read_json(current_app.config["TEMPLATES_PATH"], [])
    new_tpls = [t for t in tpls if t.get("id") != tpl_id]
    if len(new_tpls) < len(tpls):
        write_json(current_app.config["TEMPLATES_PATH"], new_tpls)
        return jsonify({"status": "deleted"})
    return jsonify({"error": "not_found"}), 404

@config_bp.route("/llm/generate", methods=["POST"])
@check_auth
def llm_generate():
    """
    LLM-Generierung direkt aufrufen (Proxy für Frontend)
    """
    data = request.get_json()
    prompt = data.get("prompt")
    if not prompt:
        return jsonify({"error": "missing_prompt"}), 400
    
    # LLM-Konfiguration kann optional mitgegeben werden, sonst Defaults des Agenten
    cfg = data.get("config") or {}
    
    # Falls der Agent selbst eine LLM-Konfiguration gespeichert hat, nutzen wir diese als Basis
    agent_cfg = current_app.config.get("AGENT_CONFIG", {}).get("llm_config", {})
    
    # Priorität: Request > Agent Config > Settings Defaults
    provider = cfg.get("provider") or agent_cfg.get("provider")
    model = cfg.get("model") or agent_cfg.get("model")
    base_url = cfg.get("base_url") or agent_cfg.get("base_url")
    api_key = cfg.get("api_key") or agent_cfg.get("api_key")

    response = generate_text(
        prompt=prompt,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key
    )
    
    return jsonify({"response": response})
