import time
import jwt
import logging
import re
import secrets
from flask import Blueprint, request, g
from agent.common.errors import api_response
from werkzeug.security import generate_password_hash, check_password_hash
from agent.config import settings
from agent.auth import check_user_auth, admin_required
from agent.common.audit import log_audit
from agent.repository import user_repo, refresh_token_repo, login_attempt_repo, password_history_repo, banned_ip_repo
from agent.db_models import UserDB, RefreshTokenDB, PasswordHistoryDB
from agent.common.mfa import (
    generate_mfa_secret,
    get_totp_uri,
    verify_totp,
    generate_qr_code_base64,
    encrypt_secret,
    decrypt_secret
)

# Reduziert MFA-Log-Noise: speichert Zeitstempel des letzten WARN-Logs pro User/IP
MFA_WARN_LAST = {}

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
    if len(password) < settings.auth_password_min_length:
        return False, f"Password must be at least {settings.auth_password_min_length} characters long."
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
    # 1. Globalen IP-Ban prüfen
    if banned_ip_repo.is_banned(ip):
        return True

    # 2. Kurzfristiges Rate Limiting: 10 Versuche in 1 Minute
    count_1m = login_attempt_repo.get_recent_count(ip, window_seconds=settings.auth_rate_limit_window_short_seconds)
    if count_1m >= settings.auth_rate_limit_max_attempts_short:
        return True

    # 3. Langfristiges Rate Limiting (Fail2Ban-style): 50 Versuche in 1 Stunde -> 24h Sperre
    count_1h = login_attempt_repo.get_recent_count(ip, window_seconds=settings.auth_rate_limit_window_long_seconds)
    if count_1h >= settings.auth_rate_limit_max_attempts_long:
        logging.critical(
            f"IP {ip} banned for {settings.auth_ip_ban_duration_seconds}s due to "
            f"{settings.auth_rate_limit_max_attempts_long}+ failed attempts in "
            f"{settings.auth_rate_limit_window_long_seconds}s."
        )
        banned_ip_repo.ban_ip(
            ip,
            duration_seconds=settings.auth_ip_ban_duration_seconds,
            reason=(
                f"{settings.auth_rate_limit_max_attempts_long}+ failed attempts in "
                f"{settings.auth_rate_limit_window_long_seconds}s"
            ),
        )
        log_audit("ip_banned", {"ip": ip, "reason": "excessive_failed_logins"})
        return True

    return False

def check_password_history(username, new_password):
    """
    Prüft, ob das neue Passwort in den letzten 3 Passwörtern enthalten ist.
    """
    history = password_history_repo.get_by_username(username, limit=settings.auth_password_history_limit)
    for entry in history:
        if check_password_hash(entry.password_hash, new_password):
            return True
    return False

def record_attempt(ip):
    login_attempt_repo.record_attempt(ip)

def notify_lockout(username):
    """
    Simuliert eine Benachrichtigung bei Account-Sperrung.
    """
    logging.critical(f"ACCOUNT LOCKED: User {username} has been locked out due to multiple failed attempts.")
    log_audit("account_lockout", {"username": username, "severity": "CRITICAL"})
    # Simulation E-Mail
    logging.info(f"Sending notification email to admin and user {username}")

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
            mfa_token:
              type: string
              description: 6-stelliger TOTP Token oder 8-stelliger Backup-Code (falls MFA aktiv)
    responses:
      200:
        description: Login erfolgreich (oder MFA-Token erforderlich)
        schema:
          type: object
          properties:
            access_token:
              type: string
            refresh_token:
              type: string
            username:
              type: string
            role:
              type: string
            mfa_required:
              type: boolean
              description: True, wenn MFA-Token nachgeliefert werden muss
      400:
        description: Fehlende Parameter
      401:
        description: Ungültige Anmeldedaten oder ungültiger MFA-Token
      403:
        description: Account gesperrt
      429:
        description: Zu viele Versuche
    """
    ip = request.remote_addr
    if is_rate_limited(ip):
        logging.warning(f"Rate limit exceeded for login attempts from {ip}")
        return api_response(status="error", message="Too many login attempts. Please try again later.", code=429)

    data = request.json
    username = data.get("username")
    password = data.get("password")
    mfa_token = data.get("mfa_token")

    if not username or not password:
        record_attempt(ip)
        return api_response(status="error", message="Missing username or password", code=400)

    user = user_repo.get_by_username(username)

    if user:
        # Prüfen, ob Account gesperrt ist
        if user.lockout_until and user.lockout_until > time.time():
            record_attempt(ip)
            remaining = int(user.lockout_until - time.time())
            return api_response(status="error", message=f"Account is locked. Please try again in {remaining} seconds.", code=403)

    if user and check_password_hash(user.password_hash, password):
        # Falls MFA aktiviert ist, aber kein Token mitgeliefert wurde
        if user.mfa_enabled and not mfa_token:
            return api_response(data={
                "mfa_required": True,
                "username": username
            }) # 200 OK, aber ohne Token

        # Falls MFA aktiviert ist und Token mitgeliefert wurde
        if user.mfa_enabled and mfa_token:
            # 1. TOTP prüfen
            is_valid_totp = verify_totp(decrypt_secret(user.mfa_secret), mfa_token)

            # 2. Backup-Code prüfen (falls TOTP ungültig)
            is_valid_backup = False
            if not is_valid_totp and user.mfa_backup_codes:
                for idx, hashed_code in enumerate(user.mfa_backup_codes):
                    if check_password_hash(hashed_code, mfa_token):
                        is_valid_backup = True
                        # Code verbrauchen
                        user.mfa_backup_codes.pop(idx)
                        log_audit("mfa_backup_code_used", {"username": username})
                        break

            if not is_valid_totp and not is_valid_backup:
                record_attempt(ip)
                # Fehlversuch für Account tracken
                user.failed_login_attempts += 1
                if user.failed_login_attempts >= settings.auth_user_lockout_threshold:
                    user.lockout_until = time.time() + settings.auth_user_lockout_duration_seconds
                    notify_lockout(username)
                user_repo.save(user)

                # Reduziere Log-Noise durch einfaches Rate-Limiting pro User/IP (60s Fenster)
                now = time.time()
                key = (username or "unknown", ip or "unknown")
                last_ts = MFA_WARN_LAST.get(key, 0)
                if now - last_ts > 60:
                    logging.warning(f"Invalid MFA token for user: {username}")
                    MFA_WARN_LAST[key] = now
                else:
                    logging.debug(f"Invalid MFA token (suppressed, rate-limited) for user: {username}")
                return api_response(status="error", message="Invalid MFA token", code=401)

        # Bei Erfolg Zähler zurücksetzen
        login_attempt_repo.delete_by_ip(ip)
        user.failed_login_attempts = 0
        user.lockout_until = None
        user_repo.save(user)

        # Access Token (JWT) generieren
        payload = {
            "sub": username,
            "role": user.role,
            "mfa_enabled": user.mfa_enabled,
            "iat": int(time.time()),
            "exp": int(time.time()) + settings.auth_access_token_ttl_seconds
        }
        token = jwt.encode(payload, settings.secret_key, algorithm="HS256")

        # Refresh Token generieren (einfach ein langer Zufallsstring)
        refresh_token = secrets.token_urlsafe(64)

        # Refresh Token speichern
        refresh_token_repo.save(RefreshTokenDB(
            token=refresh_token,
            username=username,
            expires_at=time.time() + settings.auth_refresh_token_ttl_seconds
        ))

        logging.info(f"User login successful: {username}")
        log_audit("login_success", {"username": username})
        return api_response(data={
            "access_token": token,
            "refresh_token": refresh_token,
            "username": username,
            "role": user.role,
            "mfa_required": user.mfa_enabled
        })

    record_attempt(ip)
    if user:
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.auth_user_lockout_threshold:
            user.lockout_until = time.time() + settings.auth_user_lockout_duration_seconds
            notify_lockout(username)
        user_repo.save(user)

    logging.warning(f"Failed login attempt for user: {username} from {ip}")
    log_audit("login_failed", {"username": username})
    return api_response(status="error", message="Invalid credentials", code=401)

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
        schema:
          type: object
          properties:
            access_token:
              type: string
            refresh_token:
              type: string
            username:
              type: string
            role:
              type: string
      400:
        description: Fehlendes Refresh Token
      401:
        description: Ungültiges oder abgelaufenes Refresh Token
      429:
        description: Zu viele Versuche
    """
    ip = request.remote_addr
    if is_rate_limited(ip):
        logging.warning(f"Rate limit exceeded for refresh-token attempts from {ip}")
        return api_response(status="error", message="Too many login attempts. Please try again later.", code=429)

    data = request.json
    refresh_token = data.get("refresh_token")

    if not refresh_token:
        record_attempt(ip)
        return api_response(status="error", message="Missing refresh token", code=400)

    token_obj = refresh_token_repo.get_by_token(refresh_token)

    if not token_obj or token_obj.expires_at < time.time():
        record_attempt(ip)
        if token_obj:
            refresh_token_repo.delete(refresh_token)
        return api_response(status="error", message="Invalid or expired refresh token", code=401)

    username = token_obj.username
    user = user_repo.get_by_username(username)

    if not user:
        return api_response(status="error", message="User no longer exists", code=401)

    # Neuen Access Token generieren
    payload = {
        "sub": username,
        "role": user.role,
        "mfa_enabled": user.mfa_enabled,
        "iat": int(time.time()),
        "exp": int(time.time()) + settings.auth_access_token_ttl_seconds
    }
    new_token = jwt.encode(payload, settings.secret_key, algorithm="HS256")

    # Refresh Token Rotation: Altes Token löschen und neues generieren
    refresh_token_repo.delete(refresh_token)
    new_refresh_token = secrets.token_urlsafe(64)
    refresh_token_repo.save(RefreshTokenDB(
        token=new_refresh_token,
        username=username,
        expires_at=time.time() + settings.auth_refresh_token_ttl_seconds
    ))

    return api_response(data={
        "access_token": new_token,
        "refresh_token": new_refresh_token,
        "username": username,
        "role": user.role
    })

@auth_bp.route("/me", methods=["GET"])
@check_user_auth
def get_me():
    """
    Gibt Informationen über den aktuell angemeldeten Benutzer zurück.
    ---
    tags:
      - Auth
    responses:
      200:
        description: Benutzerinformationen
        schema:
          type: object
          properties:
            username:
              type: string
            role:
              type: string
            mfa_enabled:
              type: boolean
      401:
        description: Nicht authentifiziert
      404:
        description: Benutzer nicht gefunden
    """
    username = g.user.get("sub")
    user = user_repo.get_by_username(username)
    if not user:
        return api_response(status="error", message="User not found", code=404)

    return api_response(data={
        "username": user.username,
        "role": user.role,
        "mfa_enabled": user.mfa_enabled
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
        return api_response(status="error", message="Missing old or new password", code=400)

    is_valid, error_msg = validate_password_complexity(new_password)
    if not is_valid:
        return api_response(status="error", message=error_msg, code=400)

    username = g.user["sub"]
    user = user_repo.get_by_username(username)

    if not user or not check_password_hash(user.password_hash, old_password):
        return api_response(status="error", message="Invalid old password", code=401)

    if check_password_history(username, new_password):
        return api_response(status="error", message="You cannot reuse your last 3 passwords.", code=400)

    # Aktuelles Passwort in Historie speichern
    password_history_repo.save(PasswordHistoryDB(
        username=username,
        password_hash=user.password_hash
    ))

    # Passwort aktualisieren
    user.password_hash = generate_password_hash(new_password)
    user_repo.save(user)

    # Alle Refresh Tokens für diesen User entwerten (Sicherheit)
    refresh_token_repo.delete_by_username(username)

    logging.info(f"Password changed for user: {username}")
    log_audit("password_changed", {"target_user": username})
    return api_response(data={"status": "password_changed"})

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
        schema:
          type: object
          properties:
            secret:
              type: string
            qr_code:
              type: string
      400:
        description: MFA bereits aktiviert oder Benutzer nicht gefunden
      401:
        description: Nicht authentifiziert
    """
    username = g.user["sub"]
    user = user_repo.get_by_username(username)

    if not user:
        return api_response(status="error", message="User not found", code=404)

    if user.mfa_enabled:
        return api_response(status="error", message="MFA is already enabled. Disable it first.", code=400)

    secret = generate_mfa_secret()
    user.mfa_secret = encrypt_secret(secret)
    user_repo.save(user)

    uri = get_totp_uri(username, secret)
    qr_code = generate_qr_code_base64(uri)

    return api_response(data={
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
              description: 6-stelliger TOTP Token
    responses:
      200:
        description: MFA erfolgreich verifiziert und aktiviert
        schema:
          type: object
          properties:
            status:
              type: string
            access_token:
              type: string
              description: Neuer Access Token mit MFA-Flag
            backup_codes:
              type: array
              items:
                type: string
              description: Einmal-Backup-Codes (werden nur einmalig angezeigt!)
      400:
        description: Ungültiger Token oder MFA nicht eingerichtet
      429:
        description: Zu viele Versuche
      401:
        description: Nicht authentifiziert
    """
    ip = request.remote_addr
    if is_rate_limited(ip):
        logging.warning(f"Rate limit exceeded for MFA verification from {ip}")
        return api_response(status="error", message="Too many attempts. Please try again later.", code=429)

    data = request.json
    token = data.get("token")

    if not token:
        record_attempt(ip)
        return api_response(status="error", message="Missing token", code=400)

    username = g.user["sub"]
    user = user_repo.get_by_username(username)

    if not user or not user.mfa_secret:
        return api_response(status="error", message="MFA not set up", code=400)

    if verify_totp(decrypt_secret(user.mfa_secret), token):
        login_attempt_repo.delete_by_ip(ip)
        user.mfa_enabled = True
        user.failed_login_attempts = 0 # Zurücksetzen bei Erfolg

        # Backup-Codes generieren
        backup_codes = [secrets.token_hex(4) for _ in range(settings.auth_mfa_backup_code_count)]
        user.mfa_backup_codes = [generate_password_hash(bc) for bc in backup_codes]

        user_repo.save(user)
        log_audit("mfa_enabled", {"username": username})

        # Neuen JWT generieren, damit mfa_enabled: true sofort drin ist
        payload = {
            "sub": username,
            "role": user.role,
            "mfa_enabled": True,
            "iat": int(time.time()),
            "exp": int(time.time()) + settings.auth_access_token_ttl_seconds
        }
        new_token = jwt.encode(payload, settings.secret_key, algorithm="HS256")

        return api_response(data={
            "status": "mfa_enabled",
            "access_token": new_token,
            "backup_codes": backup_codes
        })
    else:
        record_attempt(ip)
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.auth_user_lockout_threshold:
            user.lockout_until = time.time() + settings.auth_user_lockout_duration_seconds
            notify_lockout(username)
        user_repo.save(user)
        return api_response(status="error", message="Invalid token", code=400)

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

        # Neuen JWT generieren
        payload = {
            "sub": username,
            "role": user.role,
            "mfa_enabled": False,
            "iat": int(time.time()),
            "exp": int(time.time()) + settings.auth_access_token_ttl_seconds
        }
        new_token = jwt.encode(payload, settings.secret_key, algorithm="HS256")

        return api_response(data={
            "status": "mfa_disabled",
            "access_token": new_token
        })
    return api_response(status="error", message="User not found", code=404)

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
    return api_response(data=safe_users)

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
        return api_response(status="error", message="Missing username or password", code=400)

    is_valid, error_msg = validate_password_complexity(password)
    if not is_valid:
        return api_response(status="error", message=error_msg, code=400)

    if user_repo.get_by_username(username):
        return api_response(status="error", message="User already exists", code=400)

    user_repo.save(UserDB(
        username=username,
        password_hash=generate_password_hash(password),
        role=role
    ))

    logging.info(f"User created by admin: {username} (role: {role})")
    log_audit("user_created", {"new_user": username, "role": role})
    return api_response(data={"status": "user_created", "username": username})

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
        return api_response(status="error", message="Cannot delete main admin", code=400)

    if not user_repo.delete(username):
        return api_response(status="error", message="User not found", code=404)

    # Refresh Tokens für diesen User auch löschen
    refresh_token_repo.delete_by_username(username)

    logging.info(f"User deleted by admin: {username}")
    log_audit("user_deleted", {"deleted_user": username})
    return api_response(data={"status": "user_deleted"})

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
        return api_response(status="error", message="Missing new_password", code=400)

    is_valid, error_msg = validate_password_complexity(new_password)
    if not is_valid:
        return api_response(status="error", message=error_msg, code=400)

    user = user_repo.get_by_username(username)
    if not user:
        return api_response(status="error", message="User not found", code=404)

    if check_password_history(username, new_password):
        return api_response(status="error", message="User cannot reuse their last 3 passwords.", code=400)

    # Aktuelles Passwort in Historie speichern
    password_history_repo.save(PasswordHistoryDB(
        username=username,
        password_hash=user.password_hash
    ))

    user.password_hash = generate_password_hash(new_password)
    user_repo.save(user)

    # Refresh Tokens für diesen User entwerten
    refresh_token_repo.delete_by_username(username)

    logging.info(f"Password reset by admin for user: {username}")
    log_audit("password_reset", {"target_user": username})
    return api_response(data={"status": "password_reset"})

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
        return api_response(status="error", message="Missing role", code=400)

    if role not in ["admin", "user"]:
        return api_response(status="error", message="Invalid role", code=400)

    user = user_repo.get_by_username(username)
    if not user:
        return api_response(status="error", message="User not found", code=404)

    user.role = role
    user_repo.save(user)

    logging.info(f"Role updated by admin for user {username}: {role}")
    log_audit("user_role_updated", {"target_user": username, "new_role": role})
    return api_response(data={"status": "role_updated", "username": username, "role": role})
