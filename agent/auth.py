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
from agent.common.errors import PermanentError, api_response

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
            return api_response(status="error", message="unauthorized", data={"details": "Missing Authorization (header or token param)"}, code=401)
        
        try:
            # Wenn der Token ein JWT ist, versuchen wir zuerst AGENT_TOKEN, dann User-JWT.
            if provided_token.count(".") == 2:
                try:
                    payload = jwt.decode(provided_token, token, algorithms=["HS256"], leeway=30)
                    g.auth_payload = payload
                    g.is_admin = True  # AGENT_TOKEN berechtigt zu allem
                except jwt.PyJWTError:
                    payload = jwt.decode(provided_token, settings.secret_key, algorithms=["HS256"], leeway=30)
                    g.user = payload
                    g.is_admin = payload.get("role") == "admin"
            else:
                if provided_token != token:
                    raise Exception("Invalid static token")
                g.is_admin = True  # Statischer AGENT_TOKEN berechtigt zu allem
        except Exception as e:
            logging.warning(f"Authentifizierungsfehler von {request.remote_addr}: {e}")
            return api_response(status="error", message="unauthorized", data={"details": "Invalid token"}, code=401)
            
        return f(*args, **kwargs)
    return wrapper

def check_user_auth(f):
    """Prüft auf einen gültigen Benutzer-JWT."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return api_response(status="error", message="User authentication required", code=401)
            
        token = auth_header.split(" ")[1]
        try:
            # Benutzer-Tokens werden mit settings.secret_key signiert
            payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"], leeway=30)
            g.user = payload
            g.is_admin = payload.get("role") == "admin"
        except jwt.ExpiredSignatureError:
            return api_response(status="error", message="Token expired", code=401)
        except jwt.InvalidTokenError:
            return api_response(status="error", message="Invalid token", code=401)
            
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    """Erfordert Admin-Rechte (entweder via AGENT_TOKEN oder via User-Role)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Wir prüfen zuerst, ob bereits eine Authentifizierung stattgefunden hat
        if not hasattr(g, "is_admin"):
            # Falls nicht, versuchen wir beide Authentifizierungsmethoden
            
            # 1. Versuch: AGENT_TOKEN
            token = current_app.config.get("AGENT_TOKEN")
            auth_header = request.headers.get("Authorization")
            provided_token = None
            if auth_header and auth_header.startswith("Bearer "):
                provided_token = auth_header.split(" ")[1]
            elif request.args.get("token"):
                provided_token = request.args.get("token")
                
            if provided_token:
                try:
                    if provided_token.count(".") == 2 and token:
                        jwt.decode(provided_token, token, algorithms=["HS256"], leeway=30)
                        g.is_admin = True
                    elif provided_token == token and token:
                        g.is_admin = True
                except jwt.PyJWTError:
                    pass
            
            # 2. Versuch: User JWT (wenn noch kein Admin via AGENT_TOKEN)
            if not getattr(g, "is_admin", False) and provided_token:
                try:
                    payload = jwt.decode(provided_token, settings.secret_key, algorithms=["HS256"], leeway=30)
                    g.user = payload
                    if payload.get("role") == "admin":
                        g.is_admin = True
                except jwt.PyJWTError:
                    pass
                    
        if not getattr(g, "is_admin", False):
            return api_response(status="error", message="forbidden", data={"details": "Admin privileges required"}, code=403)
            
        return f(*args, **kwargs)
    return decorated

def rotate_token():
    """Generiert einen neuen Secret-Token und aktualisiert die Config sowie die Persistenz."""
    new_secret = secrets.token_urlsafe(32)
    
    # Synchronisation mit dem Hub versuchen, BEVOR wir den Token lokal festschreiben
    hub_url = settings.hub_url
    agent_name = current_app.config.get("AGENT_NAME")
    if hub_url and agent_name:
        success = register_with_hub(
            hub_url=hub_url,
            agent_name=agent_name,
            port=settings.port,
            token=new_secret,
            role=current_app.config.get("ROLE", "worker")
        )
        if not success:
            logging.error("Token-Rotation abgebrochen: Registrierung am Hub fehlgeschlagen.")
            raise PermanentError("Token-Rotation fehlgeschlagen: Synchronisation mit Hub nicht möglich.")

    current_app.config["AGENT_TOKEN"] = new_secret
    
    # Persistieren
    try:
        settings.save_agent_token(new_secret)
    except Exception as e:
        # Hier loggen wir nur, da der Hub den Token bereits hat. 
        # Ein Rollback wäre jetzt noch komplizierter.
        logging.error(f"Fehler beim Persistieren des Tokens: {e}")
            
    logging.info("Agent Secret/Token wurde rotiert.")
    return new_secret
