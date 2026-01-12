import time
import logging
import concurrent.futures
import os
from flask import Blueprint, jsonify, current_app, request, g
from agent.metrics import generate_latest, CONTENT_TYPE_LATEST
from agent.utils import rate_limit, validate_request, read_json, write_json, _http_get
from agent.models import AgentRegisterRequest
from agent.auth import check_auth, rotate_token
from agent.config import settings
from agent.common.http import get_default_client

system_bp = Blueprint("system", __name__)
http_client = get_default_client()

@system_bp.route("/health", methods=["GET"])
def health():
    checks = {}
    
    # 1. Shell Check
    from agent.shell import get_shell
    try:
        shell = get_shell()
        is_alive = shell.process is not None and shell.process.poll() is None
        checks["shell"] = {"status": "ok" if is_alive else "down"}
    except Exception as e:
        checks["shell"] = {"status": "error", "message": str(e)}

    # 2. LLM Providers Check
    llm_checks = {}
    providers = ["ollama", "lmstudio", "openai", "anthropic"]
    
    def _check_provider(p):
        url = getattr(settings, f"{p}_url", None)
        if not url:
            return p, None
        try:
            # Schneller Check ob der Service erreichbar ist
            res = http_client.get(url, timeout=1.0, return_response=True)
            if res:
                return p, ("ok" if res.status_code < 500 else "unstable")
            else:
                return p, "unreachable"
        except Exception:
            return p, "error"

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(providers)) as executor:
        futures = [executor.submit(_check_provider, p) for p in providers]
        for future in concurrent.futures.as_completed(futures):
            p, status = future.result()
            if status:
                llm_checks[p] = status
    
    if llm_checks:
        checks["llm_providers"] = llm_checks

    return jsonify({
        "status": "ok", 
        "agent": current_app.config.get("AGENT_NAME"),
        "checks": checks
    })

@system_bp.route("/ready", methods=["GET"])
def readiness_check():
    results = {}
    is_ready = True
    
    def _check_controller():
        try:
            start = time.time()
            res = http_client.get(settings.controller_url, timeout=settings.http_timeout, return_response=True)
            if res:
                return "controller", {
                    "status": "ok" if res.status_code < 500 else "unstable",
                    "latency": round(time.time() - start, 3),
                    "code": res.status_code
                }
            else:
                return "controller", {"status": "error", "message": "No response from controller"}
        except Exception as e:
            return "controller", {"status": "error", "message": str(e)}

    def _check_llm():
        provider = settings.default_provider
        url = getattr(settings, f"{provider}_url", None)
        if not url:
            return "llm", None
        try:
            start = time.time()
            res = http_client.get(url, timeout=settings.http_timeout, return_response=True)
            if res:
                return "llm", {
                    "provider": provider,
                    "status": "ok" if res.status_code < 500 else "unstable",
                    "latency": round(time.time() - start, 3),
                    "code": res.status_code
                }
            else:
                return "llm", {"status": "error", "message": f"No response from LLM provider {provider}"}
        except Exception as e:
            return "llm", {"status": "error", "message": str(e)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(_check_controller), executor.submit(_check_llm)]
        for future in concurrent.futures.as_completed(futures):
            key, result = future.result()
            if result:
                results[key] = result
                if result.get("status") == "error":
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
    
    # Registrierungs-Token prüfen, falls konfiguriert
    if settings.registration_token:
        provided_token = data.get("registration_token")
        if provided_token != settings.registration_token:
            logging.warning(f"Abgelehnte Registrierung für {data.get('name')}: Ungültiger Registrierungs-Token")
            return jsonify({"error": "Invalid or missing registration token"}), 401

    name = data.get("name")
    url = data.get("url")
    
    # URL Validierung: Prüfen ob der Agent erreichbar ist
    try:
        # Wir versuchen den /health Endpunkt des Agenten oder die Basis-URL zu erreichen
        check_url = f"{url.rstrip('/')}/health"
        res = http_client.get(check_url, timeout=2.0, return_response=True)
        if not res or res.status_code >= 500:
            # Fallback auf Basis-URL falls /health nicht existiert
            res = http_client.get(url, timeout=2.0, return_response=True)
            
        if not res:
            return jsonify({"error": f"Agent URL {url} is unreachable"}), 400
    except Exception as e:
        return jsonify({"error": f"Validation failed: {str(e)}"}), 400

    agents = read_json(current_app.config["AGENTS_PATH"], {})
    agents[name] = {
        "url": url,
        "role": data.get("role", "worker"),
        "token": data.get("token"),
        "last_seen": time.time(),
        "status": "online"
    }
    write_json(current_app.config["AGENTS_PATH"], agents)
    logging.info(f"Agent registriert: {name} ({url})")
    return jsonify({"status": "registered"})

@system_bp.route("/agents", methods=["GET"])
@check_auth
def list_agents():
    agents = read_json(current_app.config["AGENTS_PATH"], {})
    now = time.time()
    changed = False
    
    timeout = getattr(settings, "agent_offline_timeout", 300)
    
    for name, info in agents.items():
        if info.get("status") == "online":
            last_seen = info.get("last_seen", 0)
            if now - last_seen > timeout:
                info["status"] = "offline"
                changed = True
                logging.info(f"Agent {name} ist jetzt offline (letzte Meldung vor {round(now - last_seen)}s)")
    
    if changed:
        write_json(current_app.config["AGENTS_PATH"], agents)
        
    return jsonify(agents)

@system_bp.route("/rotate-token", methods=["POST"])
@check_auth
def do_rotate_token():
    new_token = rotate_token()
    return jsonify({"status": "rotated", "new_token": new_token})

def check_all_agents_health(app):
    """Prüft den Status aller registrierten Agenten parallel."""
    with app.app_context():
        agents_path = app.config.get("AGENTS_PATH")
        if not agents_path or not os.path.exists(agents_path):
            return
            
        agents = read_json(agents_path, {})
        if not agents:
            return
            
        changed = False
        now = time.time()

        def _check_agent(name, info):
            url = info.get("url")
            if not url:
                return name, None
            try:
                check_url = f"{url.rstrip('/')}/health"
                res = http_client.get(check_url, timeout=3.0, return_response=True)
                if not res or res.status_code >= 500:
                    res = http_client.get(url, timeout=3.0, return_response=True)
                return name, ("online" if res and res.status_code < 500 else "offline")
            except Exception:
                return name, "offline"

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(_check_agent, n, i) for n, i in agents.items()]
            for future in concurrent.futures.as_completed(futures):
                name, new_status = future.result()
                if not new_status: continue
                
                info = agents[name]
                if info.get("status") != new_status:
                    logging.info(f"Agent {name} Statusänderung: {info.get('status')} -> {new_status}")
                    info["status"] = new_status
                    changed = True
                if new_status == "online":
                    info["last_seen"] = now

        if changed:
            write_json(agents_path, agents)

import os
