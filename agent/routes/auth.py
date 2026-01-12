import time
import jwt
import logging
import os
from flask import Blueprint, jsonify, request, current_app, g
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from agent.utils import read_json, write_json
from agent.config import settings
from agent.auth import check_user_auth, admin_required

auth_bp = Blueprint("auth", __name__)

# Einfaches In-Memory Rate Limiting für Login
login_attempts = {} # {ip: [timestamps]}

def is_rate_limited(ip):
    now = time.time()
    # Letzte 5 Versuche in der letzten Minute
    attempts = login_attempts.get(ip, [])
    attempts = [t for t in attempts if now - t < 60]
    login_attempts[ip] = attempts
    if len(attempts) >= 5:
        return True
    return False

def record_attempt(ip):
    now = time.time()
    if ip not in login_attempts:
        login_attempts[ip] = []
    login_attempts[ip].append(now)

def _get_user_path():
    return os.path.join(current_app.config.get("DATA_DIR", "data"), "users.json")

def _get_refresh_token_path():
    return os.path.join(current_app.config.get("DATA_DIR", "data"), "refresh_tokens.json")

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

def _save_users(users):
    write_json(_get_user_path(), users)

def _load_refresh_tokens():
    return read_json(_get_refresh_token_path(), {})

def _save_refresh_tokens(tokens):
    write_json(_get_refresh_token_path(), tokens)

@auth_bp.route("/login", methods=["POST"])
def login():
    ip = request.remote_addr
    if is_rate_limited(ip):
        logging.warning(f"Rate limit exceeded for login attempts from {ip}")
        return jsonify({"error": "Too many login attempts. Please try again later."}), 429
        
    data = request.json
    username = data.get("username")
    password = data.get("password")
    
    if not username or not password:
        record_attempt(ip)
        return jsonify({"error": "Missing username or password"}), 400
        
    users = _load_users()
    user = users.get(username)
    
    if user and check_password_hash(user["password"], password):
        # Bei Erfolg Zähler zurücksetzen
        if ip in login_attempts:
            del login_attempts[ip]
            
        # Access Token (JWT) generieren
        payload = {
            "sub": username,
            "role": user.get("role", "user"),
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600 # 1h gültig
        }
        token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
        
        # Refresh Token generieren (einfach ein langer Zufallsstring)
        import secrets
        refresh_token = secrets.token_urlsafe(64)
        
        # Refresh Token speichern
        tokens = _load_refresh_tokens()
        tokens[refresh_token] = {
            "username": username,
            "expires_at": time.time() + 3600 * 24 * 7 # 7 Tage gültig
        }
        _save_refresh_tokens(tokens)
        
        logging.info(f"User login successful: {username}")
        return jsonify({
            "token": token,
            "refresh_token": refresh_token,
            "username": username,
            "role": user.get("role", "user")
        })
    
    record_attempt(ip)
    logging.warning(f"Failed login attempt for user: {username} from {ip}")
    return jsonify({"error": "Invalid credentials"}), 401

@auth_bp.route("/refresh-token", methods=["POST"])
def refresh():
    data = request.json
    refresh_token = data.get("refresh_token")
    
    if not refresh_token:
        return jsonify({"error": "Missing refresh token"}), 400
        
    tokens = _load_refresh_tokens()
    token_info = tokens.get(refresh_token)
    
    if not token_info or token_info["expires_at"] < time.time():
        if refresh_token in tokens:
            del tokens[refresh_token]
            _save_refresh_tokens(tokens)
        return jsonify({"error": "Invalid or expired refresh token"}), 401
        
    username = token_info["username"]
    users = _load_users()
    user = users.get(username)
    
    if not user:
        return jsonify({"error": "User no longer exists"}), 401
        
    # Neuen Access Token generieren
    payload = {
        "sub": username,
        "role": user.get("role", "user"),
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600
    }
    new_token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    
    return jsonify({
        "token": new_token,
        "username": username,
        "role": user.get("role", "user")
    })

@auth_bp.route("/change-password", methods=["POST"])
@check_user_auth
def change_password():
    data = request.json
    old_password = data.get("old_password")
    new_password = data.get("new_password")
    
    if not old_password or not new_password:
        return jsonify({"error": "Missing old or new password"}), 400
        
    username = g.user["sub"]
    users = _load_users()
    user = users.get(username)
    
    if not user or not check_password_hash(user["password"], old_password):
        return jsonify({"error": "Invalid old password"}), 401
        
    # Passwort aktualisieren
    user["password"] = generate_password_hash(new_password)
    _save_users(users)
    
    # Alle Refresh Tokens für diesen User entwerten (Sicherheit)
    tokens = _load_refresh_tokens()
    new_tokens = {k: v for k, v in tokens.items() if v["username"] != username}
    _save_refresh_tokens(new_tokens)
    
    logging.info(f"Password changed for user: {username}")
    return jsonify({"status": "password_changed"})

@auth_bp.route("/users", methods=["GET"])
@admin_required
def get_users():
    users = _load_users()
    # Passwörter nicht mitsenden
    safe_users = []
    for username, info in users.items():
        safe_users.append({
            "username": username,
            "role": info.get("role", "user")
        })
    return jsonify(safe_users)

@auth_bp.route("/users", methods=["POST"])
@admin_required
def create_user():
    data = request.json
    username = data.get("username")
    password = data.get("password")
    role = data.get("role", "user")
    
    if not username or not password:
        return jsonify({"error": "Missing username or password"}), 400
        
    users = _load_users()
    if username in users:
        return jsonify({"error": "User already exists"}), 400
        
    users[username] = {
        "password": generate_password_hash(password),
        "role": role
    }
    _save_users(users)
    
    logging.info(f"User created by admin: {username} (role: {role})")
    return jsonify({"status": "user_created", "username": username})

@auth_bp.route("/users/<username>", methods=["DELETE"])
@admin_required
def delete_user(username):
    if username == "admin":
        return jsonify({"error": "Cannot delete main admin"}), 400
        
    users = _load_users()
    if username not in users:
        return jsonify({"error": "User not found"}), 404
        
    del users[username]
    _save_users(users)
    
    # Refresh Tokens für diesen User auch löschen
    tokens = _load_refresh_tokens()
    new_tokens = {k: v for k, v in tokens.items() if v["username"] != username}
    _save_refresh_tokens(new_tokens)
    
    logging.info(f"User deleted by admin: {username}")
    return jsonify({"status": "user_deleted"})

@auth_bp.route("/users/<username>/reset-password", methods=["POST"])
@admin_required
def reset_password(username):
    data = request.json
    new_password = data.get("new_password")
    
    if not new_password:
        return jsonify({"error": "Missing new_password"}), 400
        
    users = _load_users()
    if username not in users:
        return jsonify({"error": "User not found"}), 404
        
    users[username]["password"] = generate_password_hash(new_password)
    _save_users(users)
    
    # Refresh Tokens für diesen User entwerten
    tokens = _load_refresh_tokens()
    new_tokens = {k: v for k, v in tokens.items() if v["username"] != username}
    _save_refresh_tokens(new_tokens)
    
    logging.info(f"Password reset by admin for user: {username}")
    return jsonify({"status": "password_reset"})

@auth_bp.route("/users/<username>/role", methods=["PUT"])
@admin_required
def update_user_role(username):
    data = request.json
    role = data.get("role")
    
    if not role:
        return jsonify({"error": "Missing role"}), 400
        
    if role not in ["admin", "user"]:
        return jsonify({"error": "Invalid role"}), 400
        
    users = _load_users()
    if username not in users:
        return jsonify({"error": "User not found"}), 404
        
    users[username]["role"] = role
    _save_users(users)
    
    logging.info(f"Role updated by admin for user {username}: {role}")
    return jsonify({"status": "role_updated", "username": username, "role": role})
