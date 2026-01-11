import time
import logging
import secrets
import jwt
from flask import request, jsonify, current_app
from functools import wraps

def generate_token(payload: dict, secret: str, expires_in: int = 3600):
    """Generiert einen JWT-Token."""
    payload["exp"] = time.time() + expires_in
    return jwt.encode(payload, secret, algorithm="HS256")

def check_auth(f):
    """Decorator zur Prüfung der JWT-Authentifizierung."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = current_app.config.get("AGENT_TOKEN")
        if not token:
            return f(*args, **kwargs)
        
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return jsonify({"error": "unauthorized", "message": "Missing Authorization header"}), 401
        
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "unauthorized", "message": "Invalid Authorization format"}), 401
        
        provided_token = auth_header.split(" ")[1]
        
        try:
            # Wenn der Token ein JWT ist, validieren wir ihn gegen den AGENT_TOKEN als Secret
            # Wenn es ein einfacher statischer Token ist, vergleichen wir ihn direkt (Fallback)
            if provided_token.count(".") == 2:
                payload = jwt.decode(provided_token, token, algorithms=["HS256"])
                g.auth_payload = payload
            else:
                if provided_token != token:
                    raise Exception("Invalid static token")
        except Exception as e:
            logging.warning(f"Authentifizierungsfehler von {request.remote_addr}: {e}")
            return jsonify({"error": "unauthorized", "message": "Invalid token"}), 401
            
        return f(*args, **kwargs)
    return wrapper

def rotate_token():
    """Generiert einen neuen Secret-Token und aktualisiert die Config."""
    new_secret = secrets.token_urlsafe(32)
    current_app.config["AGENT_TOKEN"] = new_secret
    # In einer produktiven Umgebung sollte dies sicher persistiert werden
    logging.info("Agent Secret/Token wurde rotiert.")
    return new_secret

from flask import g
