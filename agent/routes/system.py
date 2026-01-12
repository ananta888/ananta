import time
import logging
from flask import Blueprint, jsonify, current_app, request, g
from agent.metrics import generate_latest, CONTENT_TYPE_LATEST
from agent.utils import rate_limit, validate_request, read_json, write_json
from agent.models import AgentRegisterRequest
from agent.auth import check_auth, rotate_token
from agent.config import settings
from agent.common.http import get_default_client

system_bp = Blueprint("system", __name__)
http_client = get_default_client()

@system_bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "agent": current_app.config.get("AGENT_NAME")})

@system_bp.route("/ready", methods=["GET"])
def readiness_check():
    results = {}
    is_ready = True
    
    # 1. Controller check
    try:
        start = time.time()
        res = http_client.get(settings.controller_url, timeout=settings.http_timeout, return_response=True)
        if res:
            results["controller"] = {
                "status": "ok" if res.status_code < 500 else "unstable",
                "latency": round(time.time() - start, 3),
                "code": res.status_code
            }
        else:
            raise Exception("No response from controller")
    except Exception as e:
        results["controller"] = {"status": "error", "message": str(e)}
        is_ready = False

    # 2. LLM Check (Default Provider)
    provider = settings.default_provider
    url = getattr(settings, f"{provider}_url", None)
    if url:
        try:
            start = time.time()
            res = http_client.get(url, timeout=settings.http_timeout, return_response=True)
            if res:
                results["llm"] = {
                    "provider": provider,
                    "status": "ok" if res.status_code < 500 else "unstable",
                    "latency": round(time.time() - start, 3),
                    "code": res.status_code
                }
            else:
                raise Exception(f"No response from LLM provider {provider}")
        except Exception as e:
            results["llm"] = {"status": "error", "message": str(e)}
            is_ready = False

    return jsonify({
        "status": "ok" if is_ready else "error",
        "ready": is_ready,
        "checks": results
    }), 200 if is_ready else 503

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
@check_auth
def list_agents():
    agents = read_json(current_app.config["AGENTS_PATH"], {})
    return jsonify(agents)

@system_bp.route("/rotate-token", methods=["POST"])
@check_auth
def do_rotate_token():
    new_token = rotate_token()
    return jsonify({"status": "rotated", "new_token": new_token})
