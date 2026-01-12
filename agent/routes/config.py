import uuid
from flask import Blueprint, jsonify, current_app, request, g
from agent.utils import validate_request, read_json, write_json
from agent.auth import check_auth

config_bp = Blueprint("config", __name__)

@config_bp.route("/config", methods=["GET"])
@check_auth
def get_config():
    return jsonify(current_app.config.get("AGENT_CONFIG", {}))

@config_bp.route("/config", methods=["POST"])
@check_auth
def set_config():
    new_cfg = request.get_json()
    if not isinstance(new_cfg, dict):
        return jsonify({"error": "invalid_json"}), 400
    
    current_cfg = current_app.config.get("AGENT_CONFIG", {})
    current_cfg.update(new_cfg)
    current_app.config["AGENT_CONFIG"] = current_cfg
    
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
