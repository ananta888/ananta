import json
import logging
import os
import secrets
import time
from functools import wraps

import jwt
from flask import current_app, g, jsonify, request

from agent.utils import _http_post, register_with_hub, write_json
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
        provided_token = None
        
        if auth_header and auth_header.startswith("Bearer "):
            provided_token = auth_header.split(" ")[1]
        elif request.args.get("token"):
            provided_token = request.args.get("token")
            
        if not provided_token:
            return jsonify({"error": "unauthorized", "message": "Missing Authorization (header or token param)"}), 401
        
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
            write_json(token_path, {
                "agent_token": new_secret,
                "last_rotation": time.time()
            }, chmod=0o600)
            logging.info(f"Agent Token wurde in {token_path} persistiert.")
        except Exception as e:
            logging.error(f"Fehler beim Persistieren des Tokens: {e}")
            
    # Synchronisation mit dem Hub
    hub_url = settings.hub_url
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
