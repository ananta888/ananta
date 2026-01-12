import time
import jwt
import logging
import os
import re
import secrets
from flask import Blueprint, jsonify, request, current_app, g
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from sqlmodel import Session, select, delete
from agent.database import engine
from agent.utils import read_json, write_json
from agent.config import settings
from agent.auth import check_user_auth, admin_required
from agent.common.audit import log_audit
from agent.repository import user_repo, refresh_token_repo, login_attempt_repo
from agent.db_models import UserDB, RefreshTokenDB
from agent.common.mfa import (
    generate_mfa_secret, 
    get_totp_uri, 
    verify_totp, 
    generate_qr_code_base64,
    encrypt_secret,
    decrypt_secret
)

auth_bp = Blueprint("auth", __name__)

# Persistentes Rate Limiting über DB

def validate_password_complexity(password):
    """
    Prüft, ob das Passwort die Komplexitätsanforderungen erfüllt:
    - Mindestens 12 Zeichen
    - Mindestens ein Großbuchstabe
    - Mindestens ein Kleinbuchstabe
    - Mindestens eine Zahl
    - Mindestens ein Sonderzeichen
    """
    if len(password) < 12:
        return False, "Password must be at least 12 characters long."
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter."
    if not re.search(r"\d", password):
        return False, "Password must contain at least one number."
    if not re.search(r"[ !@#$%^&*()_+=\-{}\[\]:;\"'<>,.?/|\\~`]", password):
        return False, "Password must contain at least one special character."
    return True, ""

def is_rate_limited(ip):
    # Letzte 10 Versuche in der letzten Minute (IP-basiert)
    count = login_attempt_repo.get_recent_count(ip, window_seconds=60)
    if count >= 10:
        return True
    return False

def record_attempt(ip):
    login_attempt_repo.record_attempt(ip)

@auth_bp.route("/login", methods=["POST"])
def login():
    """
    Benutzer-Login
    ---
    tags:
      - Auth
    parameters:
      - in: body
        name: credentials
        required: true
        schema:
          type: object
          properties:
            username:
              type: string
            password:
              type: string
    responses:
      200:
        description: Login erfolgreich
      401:
        description: Ungültige Anmeldedaten
      429:
        description: Zu viele Versuche
    """
    ip = request.remote_addr
    if is_rate_limited(ip):
        logging.warning(f"Rate limit exceeded for login attempts from {ip}")
        return jsonify({"error": "Too many login attempts. Please try again later."}), 429
        
    data = request.json
    username = data.get("username")
    password = data.get("password")
    mfa_token = data.get("mfa_token")
    
    if not username or not password:
        record_attempt(ip)
        return jsonify({"error": "Missing username or password"}), 400
        
    user = user_repo.get_by_username(username)
    
    if user:
        # Prüfen, ob Account gesperrt ist
        if user.lockout_until and user.lockout_until > time.time():
            record_attempt(ip)
            remaining = int(user.lockout_until - time.time())
            return jsonify({"error": f"Account is locked. Please try again in {remaining} seconds."}), 403

    if user and check_password_hash(user.password_hash, password):
        # Falls MFA aktiviert ist, aber kein Token mitgeliefert wurde
        if user.mfa_enabled and not mfa_token:
            return jsonify({
                "mfa_required": True,
                "username": username
            }), 200 # 200 OK, aber ohne Token
            
        # Falls MFA aktiviert ist und Token mitgeliefert wurde
        if user.mfa_enabled and mfa_token:
            if not verify_totp(decrypt_secret(user.mfa_secret), mfa_token):
                record_attempt(ip)
                # Fehlversuch für Account tracken
                user.failed_login_attempts += 1
                if user.failed_login_attempts >= 5:
                    user.lockout_until = time.time() + 900 # 15 Minuten Sperre
                user_repo.save(user)
                
                logging.warning(f"Invalid MFA token for user: {username}")
                return jsonify({"error": "Invalid MFA token"}), 401
                
        # Bei Erfolg Zähler zurücksetzen
        login_attempt_repo.delete_by_ip(ip)
        user.failed_login_attempts = 0
        user.lockout_until = None
        user_repo.save(user)
            
        # Access Token (JWT) generieren
        payload = {
            "sub": username,
            "role": user.role,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600 # 1h gültig
        }
        token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
        
        # Refresh Token generieren (einfach ein langer Zufallsstring)
        refresh_token = secrets.token_urlsafe(64)
        
        # Refresh Token speichern
        refresh_token_repo.save(RefreshTokenDB(
            token=refresh_token,
            username=username,
            expires_at=time.time() + 3600 * 24 * 7 # 7 Tage gültig
        ))
        
        logging.info(f"User login successful: {username}")
        log_audit("login_success", {"username": username})
        return jsonify({
            "access_token": token,
            "refresh_token": refresh_token,
            "username": username,
            "role": user.role,
            "mfa_required": user.mfa_enabled
        })
    
    record_attempt(ip)
    if user:
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= 5:
            user.lockout_until = time.time() + 900 # 15 Minuten Sperre
        user_repo.save(user)

    logging.warning(f"Failed login attempt for user: {username} from {ip}")
    log_audit("login_failed", {"username": username})
    return jsonify({"error": "Invalid credentials"}), 401

@auth_bp.route("/refresh-token", methods=["POST"])
def refresh():
    """
    Access Token mit Refresh Token erneuern
    ---
    tags:
      - Auth
    parameters:
      - in: body
        name: token
        required: true
        schema:
          type: object
          properties:
            refresh_token:
              type: string
    responses:
      200:
        description: Token erfolgreich erneuert
      401:
        description: Ungültiges oder abgelaufenes Refresh Token
    """
    data = request.json
    refresh_token = data.get("refresh_token")
    
    if not refresh_token:
        return jsonify({"error": "Missing refresh token"}), 400
        
    token_obj = refresh_token_repo.get_by_token(refresh_token)
    
    if not token_obj or token_obj.expires_at < time.time():
        if token_obj:
            refresh_token_repo.delete(refresh_token)
        return jsonify({"error": "Invalid or expired refresh token"}), 401
        
    username = token_obj.username
    user = user_repo.get_by_username(username)
    
    if not user:
        return jsonify({"error": "User no longer exists"}), 401
        
    # Neuen Access Token generieren
    payload = {
        "sub": username,
        "role": user.role,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600
    }
    new_token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    
    return jsonify({
        "access_token": new_token,
        "username": username,
        "role": user.role
    })

@auth_bp.route("/change-password", methods=["POST"])
@check_user_auth
def change_password():
    """
    Eigenes Passwort ändern
    ---
    tags:
      - Auth
    security:
      - Bearer: []
    parameters:
      - in: body
        name: passwords
        required: true
        schema:
          type: object
          properties:
            old_password:
              type: string
            new_password:
              type: string
    responses:
      200:
        description: Passwort erfolgreich geändert
      400:
        description: Ungültige Eingabe oder Passwort-Komplexität nicht erfüllt
      401:
        description: Altes Passwort ungültig oder nicht authentifiziert
    """
    data = request.json
    old_password = data.get("old_password")
    new_password = data.get("new_password")
    
    if not old_password or not new_password:
        return jsonify({"error": "Missing old or new password"}), 400
    
    is_valid, error_msg = validate_password_complexity(new_password)
    if not is_valid:
        return jsonify({"error": error_msg}), 400
        
    username = g.user["sub"]
    user = user_repo.get_by_username(username)
    
    if not user or not check_password_hash(user.password_hash, old_password):
        return jsonify({"error": "Invalid old password"}), 401
        
    # Passwort aktualisieren
    user.password_hash = generate_password_hash(new_password)
    user_repo.save(user)
    
    # Alle Refresh Tokens für diesen User entwerten (Sicherheit)
    refresh_token_repo.delete_by_username(username)
    
    logging.info(f"Password changed for user: {username}")
    log_audit("password_changed", {"target_user": username})
    return jsonify({"status": "password_changed"})

@auth_bp.route("/mfa/setup", methods=["POST"])
@check_user_auth
def mfa_setup():
    """
    MFA-Einrichtung starten
    ---
    tags:
      - Auth
    security:
      - Bearer: []
    responses:
      200:
        description: MFA-Geheimnis und QR-Code generiert
      400:
        description: MFA bereits aktiviert
      401:
        description: Nicht authentifiziert
    """
    username = g.user["sub"]
    user = user_repo.get_by_username(username)
    
    if not user:
        return jsonify({"error": "User not found"}), 404
        
    if user.mfa_enabled:
        return jsonify({"error": "MFA is already enabled. Disable it first."}), 400
        
    secret = generate_mfa_secret()
    user.mfa_secret = encrypt_secret(secret)
    user_repo.save(user)
    
    uri = get_totp_uri(username, secret)
    qr_code = generate_qr_code_base64(uri)
    
    return jsonify({
        "secret": secret,
        "qr_code": qr_code
    })

@auth_bp.route("/mfa/verify", methods=["POST"])
@check_user_auth
def mfa_verify():
    """
    MFA-Token verifizieren und aktivieren
    ---
    tags:
      - Auth
    security:
      - Bearer: []
    parameters:
      - in: body
        name: token
        required: true
        schema:
          type: object
          properties:
            token:
              type: string
    responses:
      200:
        description: MFA erfolgreich verifiziert und aktiviert
      400:
        description: Ungültiger Token
      429:
        description: Zu viele Versuche
      401:
        description: Nicht authentifiziert
    """
    ip = request.remote_addr
    if is_rate_limited(ip):
        logging.warning(f"Rate limit exceeded for MFA verification from {ip}")
        return jsonify({"error": "Too many attempts. Please try again later."}), 429

    data = request.json
    token = data.get("token")
    
    if not token:
        record_attempt(ip)
        return jsonify({"error": "Missing token"}), 400
        
    username = g.user["sub"]
    user = user_repo.get_by_username(username)
    
    if not user or not user.mfa_secret:
        return jsonify({"error": "MFA not set up"}), 400
        
    if verify_totp(decrypt_secret(user.mfa_secret), token):
        login_attempt_repo.delete_by_ip(ip)
        user.mfa_enabled = True
        user_repo.save(user)
        log_audit("mfa_enabled", {"username": username})
        return jsonify({"status": "mfa_enabled"})
    else:
        record_attempt(ip)
        return jsonify({"error": "Invalid token"}), 400

@auth_bp.route("/mfa/disable", methods=["POST"])
@check_user_auth
def mfa_disable():
    """
    MFA deaktivieren
    ---
    tags:
      - Auth
    security:
      - Bearer: []
    responses:
      200:
        description: MFA erfolgreich deaktiviert
      401:
        description: Nicht authentifiziert
    """
    username = g.user["sub"]
    user = user_repo.get_by_username(username)
    
    if user:
        user.mfa_enabled = False
        user.mfa_secret = None
        user_repo.save(user)
        log_audit("mfa_disabled", {"username": username})
        return jsonify({"status": "mfa_disabled"})
    return jsonify({"error": "User not found"}), 404

@auth_bp.route("/users", methods=["GET"])
@admin_required
def get_users():
    """
    Alle Benutzer auflisten
    ---
    tags:
      - Admin
    security:
      - Bearer: []
    responses:
      200:
        description: Liste der Benutzer
      403:
        description: Administratorrechte erforderlich
    """
    users = user_repo.get_all()
    # Passwörter nicht mitsenden
    safe_users = []
    for u in users:
        safe_users.append({
            "username": u.username,
            "role": u.role,
            "mfa_enabled": u.mfa_enabled
        })
    return jsonify(safe_users)

@auth_bp.route("/users", methods=["POST"])
@admin_required
def create_user():
    """
    Neuen Benutzer erstellen
    ---
    tags:
      - Admin
    security:
      - Bearer: []
    parameters:
      - in: body
        name: user
        required: true
        schema:
          type: object
          properties:
            username:
              type: string
            password:
              type: string
            role:
              type: string
              enum: [admin, user]
    responses:
      200:
        description: Benutzer erfolgreich erstellt
      400:
        description: Ungültige Eingabe, Passwort-Komplexität nicht erfüllt oder Benutzer existiert bereits
      403:
        description: Administratorrechte erforderlich
    """
    data = request.json
    username = data.get("username")
    password = data.get("password")
    role = data.get("role", "user")
    
    if not username or not password:
        return jsonify({"error": "Missing username or password"}), 400
        
    is_valid, error_msg = validate_password_complexity(password)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    if user_repo.get_by_username(username):
        return jsonify({"error": "User already exists"}), 400
        
    user_repo.save(UserDB(
        username=username,
        password_hash=generate_password_hash(password),
        role=role
    ))
    
    logging.info(f"User created by admin: {username} (role: {role})")
    log_audit("user_created", {"new_user": username, "role": role})
    return jsonify({"status": "user_created", "username": username})

@auth_bp.route("/users/<username>", methods=["DELETE"])
@admin_required
def delete_user(username):
    """
    Benutzer löschen
    ---
    tags:
      - Admin
    security:
      - Bearer: []
    parameters:
      - name: username
        in: path
        required: true
        type: string
    responses:
      200:
        description: Benutzer erfolgreich gelöscht
      400:
        description: Haupt-Admin kann nicht gelöscht werden
      404:
        description: Benutzer nicht gefunden
    """
    if username == "admin":
        return jsonify({"error": "Cannot delete main admin"}), 400
        
    if not user_repo.delete(username):
        return jsonify({"error": "User not found"}), 404
        
    # Refresh Tokens für diesen User auch löschen
    refresh_token_repo.delete_by_username(username)
    
    logging.info(f"User deleted by admin: {username}")
    log_audit("user_deleted", {"deleted_user": username})
    return jsonify({"status": "user_deleted"})

@auth_bp.route("/users/<username>/reset-password", methods=["POST"])
@admin_required
def reset_password(username):
    """
    Passwort eines Benutzers zurücksetzen (durch Admin)
    ---
    tags:
      - Admin
    security:
      - Bearer: []
    parameters:
      - name: username
        in: path
        required: true
        type: string
      - in: body
        name: password
        required: true
        schema:
          type: object
          properties:
            new_password:
              type: string
    responses:
      200:
        description: Passwort erfolgreich zurückgesetzt
      400:
        description: Ungültige Eingabe oder Passwort-Komplexität nicht erfüllt
      404:
        description: Benutzer nicht gefunden
    """
    data = request.json
    new_password = data.get("new_password")
    
    if not new_password:
        return jsonify({"error": "Missing new_password"}), 400
        
    is_valid, error_msg = validate_password_complexity(new_password)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    user = user_repo.get_by_username(username)
    if not user:
        return jsonify({"error": "User not found"}), 404
        
    user.password_hash = generate_password_hash(new_password)
    user_repo.save(user)
    
    # Refresh Tokens für diesen User entwerten
    refresh_token_repo.delete_by_username(username)
    
    logging.info(f"Password reset by admin for user: {username}")
    log_audit("password_reset", {"target_user": username})
    return jsonify({"status": "password_reset"})

@auth_bp.route("/users/<username>/role", methods=["PUT"])
@admin_required
def update_user_role(username):
    """
    Benutzerrolle aktualisieren
    ---
    tags:
      - Admin
    security:
      - Bearer: []
    parameters:
      - name: username
        in: path
        required: true
        type: string
      - in: body
        name: role
        required: true
        schema:
          type: object
          properties:
            role:
              type: string
              enum: [admin, user]
    responses:
      200:
        description: Rolle erfolgreich aktualisiert
      400:
        description: Ungültige Rolle oder fehlende Daten
      404:
        description: Benutzer nicht gefunden
    """
    data = request.json
    role = data.get("role")
    
    if not role:
        return jsonify({"error": "Missing role"}), 400
        
    if role not in ["admin", "user"]:
        return jsonify({"error": "Invalid role"}), 400
        
    user = user_repo.get_by_username(username)
    if not user:
        return jsonify({"error": "User not found"}), 404
        
    user.role = role
    user_repo.save(user)
    
    logging.info(f"Role updated by admin for user {username}: {role}")
    log_audit("user_role_updated", {"target_user": username, "new_role": role})
    return jsonify({"status": "role_updated", "username": username, "role": role})
