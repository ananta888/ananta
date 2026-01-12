import json
import logging
import os
import secrets
import time
from functools import wraps

import jwt
from flask import current_app, g, jsonify, request

from agent.utils import _http_post, register_with_hub
from agent.config import settings

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
            logging.warning("Agent läuft OHNE Authentifizierung! Setzen Sie AGENT_TOKEN für mehr Sicherheit.")
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
    """Generiert einen neuen Secret-Token und aktualisiert die Config sowie die Persistenz."""
    new_secret = secrets.token_urlsafe(32)
    current_app.config["AGENT_TOKEN"] = new_secret
    
    # Persistieren
    token_path = current_app.config.get("TOKEN_PATH")
    if token_path:
        try:
            os.makedirs(os.path.dirname(token_path), exist_ok=True)
            # Datei mit restriktiven Berechtigungen erstellen (0600)
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            try:
                fd = os.open(token_path, flags, 0o600)
                with os.fdopen(fd, 'w') as f:
                    json.dump({"agent_token": new_secret}, f)
                # Zusätzlicher chmod für Systeme, die os.open-Modus ignorieren
                os.chmod(token_path, 0o600)
            except (AttributeError, OSError):
                # Fallback für Plattformen ohne os.open/flags oder spezifische Fehler
                with open(token_path, 'w') as f:
                    json.dump({"agent_token": new_secret}, f)
                try:
                    os.chmod(token_path, 0o600)
                except Exception:
                    pass
            
            logging.info(f"Agent Token wurde in {token_path} mit restriktiven Berechtigungen persistiert.")
        except Exception as e:
            logging.error(f"Fehler beim Persistieren des Tokens: {e}")
            
    # Synchronisation mit dem Hub
    hub_url = settings.controller_url
    agent_name = current_app.config.get("AGENT_NAME")
    if hub_url and agent_name:
        register_with_hub(
            hub_url=hub_url,
            agent_name=agent_name,
            port=settings.port,
            token=new_secret,
            role=current_app.config.get("ROLE", "worker")
        )

    logging.info("Agent Secret/Token wurde rotiert.")
    return new_secret
