import time
import jwt
import logging
import os
from flask import Blueprint, jsonify, request, current_app, g
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from agent.utils import read_json, write_json
from agent.config import settings

auth_bp = Blueprint("auth", __name__)

def _get_user_path():
    return os.path.join(current_app.config.get("DATA_DIR", "data"), "users.json")

def _load_users():
    path = _get_user_path()
    # Wenn die Datei nicht existiert, erstellen wir einen Default-Admin
    if not os.path.exists(path):
        default_users = {
            "admin": {
                "password": generate_password_hash("admin"),
                "role": "admin"
            }
        }
        # Sicherstellen, dass das Verzeichnis existiert
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_json(path, default_users)
        return default_users
    return read_json(path, {})

@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.json
    username = data.get("username")
    password = data.get("password")
    
    if not username or not password:
        return jsonify({"error": "Missing username or password"}), 400
        
    users = _load_users()
    user = users.get(username)
    
    if user and check_password_hash(user["password"], password):
        # JWT Token generieren
        payload = {
            "sub": username,
            "role": user.get("role", "user"),
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600 * 24 # 24h gültig
        }
        token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
        logging.info(f"User login successful: {username}")
        return jsonify({
            "token": token,
            "username": username,
            "role": user.get("role", "user")
        })
    
    logging.warning(f"Failed login attempt for user: {username}")
    return jsonify({"error": "Invalid credentials"}), 401

def check_user_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return jsonify({"error": "User authentication required"}), 401
            
        token = auth_header.split(" ")[1]
        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
            g.user = payload
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401
            
        return f(*args, **kwargs)
    return decorated
