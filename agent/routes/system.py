import time
import logging
from flask import Blueprint, jsonify, current_app, request, g
from agent.metrics import generate_latest, CONTENT_TYPE_LATEST
from agent.utils import rate_limit, validate_request, read_json, write_json
from agent.models import AgentRegisterRequest
from agent.auth import check_auth, rotate_token

system_bp = Blueprint("system", __name__)

@system_bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "agent": current_app.config.get("AGENT_NAME")})

@system_bp.route("/metrics", methods=["GET"])
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

@system_bp.route("/register", methods=["POST"])
@rate_limit(limit=20, window=60)
@validate_request(AgentRegisterRequest)
def register_agent():
    data = g.validated_data.model_dump()
    name = data.get("name")
    
    agents = read_json(current_app.config["AGENTS_PATH"], {})
    agents[name] = {
        "url": data.get("url"),
        "role": data.get("role", "worker"),
        "token": data.get("token"),
        "last_seen": time.time(),
        "status": "online"
    }
    write_json(current_app.config["AGENTS_PATH"], agents)
    logging.info(f"Agent registriert: {name} ({data.get('url')})")
    return jsonify({"status": "registered"})

@system_bp.route("/agents", methods=["GET"])
def list_agents():
    agents = read_json(current_app.config["AGENTS_PATH"], {})
    return jsonify(agents)

@system_bp.route("/rotate-token", methods=["POST"])
@check_auth
def do_rotate_token():
    new_token = rotate_token()
    return jsonify({"status": "rotated", "new_token": new_token})
