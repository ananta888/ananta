from flask import Blueprint, request, jsonify, g, current_app
from agent.common.errors import api_response
import logging
import time
import os
from agent.auth import check_auth
from agent.redis import get_redis_client
import subprocess

import threading

audit_logger = logging.getLogger("audit")

# Rate Limiting State
RATE_LIMIT_WINDOW = 60  # Sekunden
MAX_REQUESTS_PER_WINDOW = 5
user_requests = {}  # {user_id: [timestamps]} Fallback für In-Memory

sgpt_bp = Blueprint("sgpt", __name__, url_prefix="/api/sgpt")
sgpt_lock = threading.Lock()

ALLOWED_OPTIONS = {
    "--shell", "--model", "--temperature", "--top-p", "--md", "--no-interaction", "--cache", "--no-cache"
}

def is_rate_limited(user_id: str) -> bool:
    """Prüft, ob der User das Rate Limit überschritten hat."""
    now = time.time()
    redis_client = get_redis_client()

    if redis_client:
        try:
            key = f"rate_limit:sgpt:{user_id}"
            current = redis_client.get(key)
            if current and int(current) >= MAX_REQUESTS_PER_WINDOW:
                return True
            
            pipe = redis_client.pipeline()
            pipe.incr(key)
            pipe.expire(key, RATE_LIMIT_WINDOW)
            pipe.execute()
            return False
        except Exception as e:
            logging.error(f"Redis error in rate limiting: {e}. Falling back to in-memory.")
    
    # In-Memory Fallback
    if user_id not in user_requests:
        user_requests[user_id] = [now]
        return False
    
    # Entferne veraltete Timestamps
    user_requests[user_id] = [ts for ts in user_requests[user_id] if now - ts < RATE_LIMIT_WINDOW]
    
    if len(user_requests[user_id]) >= MAX_REQUESTS_PER_WINDOW:
        return True
    
    user_requests[user_id].append(now)
    return False

# SGPT-4: Circuit Breaker für SGPT (Proxy)
SGPT_CIRCUIT_BREAKER = {
    "failures": 0,
    "last_failure": 0,
    "open": False
}
SGPT_CB_THRESHOLD = 5
SGPT_CB_RECOVERY_TIME = 60

@sgpt_bp.route("/execute", methods=["POST"])
@check_auth
def execute_sgpt():
    """
    Führt einen SGPT-Befehl aus.
    Erwartet JSON: {"prompt": "...", "options": ["--shell", "..."]}
    """
    # Circuit Breaker Prüfung
    if SGPT_CIRCUIT_BREAKER["open"]:
        if time.time() - SGPT_CIRCUIT_BREAKER["last_failure"] > SGPT_CB_RECOVERY_TIME:
            logging.info("SGPT Circuit Breaker wechselt in Halboffen-Zustand.")
            SGPT_CIRCUIT_BREAKER["open"] = False
            SGPT_CIRCUIT_BREAKER["failures"] = 0
        else:
            return api_response(status="error", message="SGPT service is temporarily unavailable (circuit breaker open).", code=503)

    # Rate Limiting
    # Versuche User-ID aus dem JWT zu bekommen, ansonsten Fallback auf IP
    user_id = request.remote_addr
    if hasattr(g, "user") and isinstance(g.user, dict):
        user_id = g.user.get("sub", g.user.get("user_id", user_id))
    elif hasattr(g, "auth_payload") and isinstance(g.auth_payload, dict):
         user_id = g.auth_payload.get("sub", user_id)
         
    if is_rate_limited(user_id):
        logging.warning(f"Rate limit exceeded for user {user_id}")
        return api_response(status="error", message="Rate limit exceeded. Please try again later.", code=429)

    data = request.json
    if not isinstance(data, dict):
        return api_response(status="error", message="Invalid JSON payload", code=400)

    prompt = data.get("prompt")
    options = data.get("options", [])
    
    if not prompt:
        return api_response(status="error", message="Missing prompt", code=400)
    
    if not isinstance(options, list):
        return api_response(status="error", message="Options must be a list", code=400)

    # SGPT-2: Validiere Optionen und schränke erlaubte Flags ein
    safe_options = []
    for opt in options:
        if opt in ALLOWED_OPTIONS:
            safe_options.append(opt)
        else:
            logging.warning(f"Rejected unsafe SGPT option: {opt}")

    # SGPT-1: Erzwinge --no-interaction um Blockieren zu verhindern
    if "--no-interaction" not in safe_options:
        safe_options.append("--no-interaction")

    # Baue Argument-Liste für Click
    args = safe_options + [prompt]
    
    logging.info(f"SGPT CLI Proxy: sgpt {' '.join(args)}")
    audit_logger.info(f"SGPT Request: prompt='{prompt}', options={safe_options}", extra={"extra_fields": {"action": "sgpt_execute", "prompt": prompt, "options": safe_options}})
    
    # SGPT-3: Thread-Sicherheit durch Lock
    with sgpt_lock:
        # Bereite Umgebungsvariablen für Subprozess vor
        env = os.environ.copy()
        
        # Nutze LMSTUDIO_URL aus der Config, falls vorhanden
        lmstudio_url = current_app.config.get("LMSTUDIO_URL")
        if lmstudio_url:
            if "/v1" in lmstudio_url:
                base_url = lmstudio_url.split("/v1")[0] + "/v1"
            else:
                base_url = lmstudio_url
            env["OPENAI_API_BASE"] = base_url
            
        if not env.get("OPENAI_API_KEY"):
            env["OPENAI_API_KEY"] = "sk-no-key-needed"

        try:
            # CLI-Aufruf via Subprozess für bessere Isolation
            result = subprocess.run(
                ["sgpt"] + args,
                capture_output=True,
                text=True,
                env=env,
                timeout=60
            )
            
            output = result.stdout
            errors = result.stderr
            
            if result.returncode != 0 and not output:
                logging.error(f"SGPT CLI Return Code {result.returncode}: {errors}")
                # Fehler registrieren
                SGPT_CIRCUIT_BREAKER["failures"] += 1
                SGPT_CIRCUIT_BREAKER["last_failure"] = time.time()
                if SGPT_CIRCUIT_BREAKER["failures"] >= SGPT_CB_THRESHOLD:
                    SGPT_CIRCUIT_BREAKER["open"] = True
                    logging.error("SGPT CIRCUIT BREAKER GEÖFFNET")
                
                return api_response(
                    status="error",
                    message=errors or f"SGPT failed with exit code {result.returncode}",
                    code=500
                )

            # Erfolg registrieren
            SGPT_CIRCUIT_BREAKER["failures"] = 0
            SGPT_CIRCUIT_BREAKER["open"] = False

            audit_logger.info(f"SGPT Success: output_len={len(output)}", extra={"extra_fields": {"action": "sgpt_success", "output_len": len(output), "error_len": len(errors)}})

            return api_response(data={
                "output": output,
                "errors": errors
            })
            
        except subprocess.TimeoutExpired:
            logging.error("SGPT CLI Timeout")
            return api_response(status="error", message="SGPT execution timed out", code=504)
        except Exception as e:
            logging.exception("Fehler beim Ausführen von SGPT")
            audit_logger.error(f"SGPT Error: {str(e)}", extra={"extra_fields": {"action": "sgpt_error", "error": str(e)}})
            return api_response(status="error", message=str(e), code=500)
