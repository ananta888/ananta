"""SPLIT-037: /login, /refresh-token, /me, /change-password routes.

Each route is registered onto the shared ``auth_bp`` blueprint via
the :func:`register_routes` entry point, which the auth shim calls
once during blueprint setup. The original Flask-Yaml docstrings
and bodies are preserved verbatim so behavior and the OpenAPI spec
stay unchanged.
"""
from __future__ import annotations

import secrets
import time

import jwt
from flask import g, request
from werkzeug.security import check_password_hash, generate_password_hash

from agent.auth import check_user_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.common.mfa import decrypt_secret, verify_totp
from agent.config import settings
from agent.db_models import PasswordHistoryDB, RefreshTokenDB
from agent.routes._auth_helpers import MFA_WARN_LAST
from agent.routes._auth_password import (
    check_password_history,
    is_rate_limited,
    notify_lockout,
    record_attempt,
    validate_password_complexity,
)


def _shim():
    # Late import: agent.routes.auth is the canonical owner of _log/_repos
    # and tests patch that path; looking the symbols up at call time keeps
    # the monkey-patch contract.
    from agent.routes import auth as _auth_shim

    return _auth_shim


def _repos():
    return _shim()._repos()


def _log():
    return _shim()._log()


def register_routes(auth_bp) -> None:
    """Attach the public session routes to the auth blueprint."""

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
            _log().warning("Rate limit exceeded for login attempts from %s", ip)
            return api_response(status="error", message="Too many login attempts. Please try again later.", code=429)

        data = request.json
        username = data.get("username")
        password = data.get("password")
        mfa_token = data.get("mfa_token")

        if not username or not password:
            record_attempt(ip)
            return api_response(status="error", message="Missing username or password", code=400)

        user = _repos().user_repo.get_by_username(username)

        if user:
            if user.lockout_until and user.lockout_until > time.time():
                record_attempt(ip)
                remaining = int(user.lockout_until - time.time())
                return api_response(
                    status="error", message=f"Account is locked. Please try again in {remaining} seconds.", code=403
                )

        if user and check_password_hash(user.password_hash, password):
            if user.mfa_enabled and not mfa_token:
                return api_response(data={"mfa_required": True, "username": username})

            if user.mfa_enabled and mfa_token:
                is_valid_totp = verify_totp(decrypt_secret(user.mfa_secret), mfa_token)

                is_valid_backup = False
                if not is_valid_totp and user.mfa_backup_codes:
                    for idx, hashed_code in enumerate(user.mfa_backup_codes):
                        if check_password_hash(hashed_code, mfa_token):
                            is_valid_backup = True
                            user.mfa_backup_codes.pop(idx)
                            log_audit("mfa_backup_code_used", {"username": username})
                            break

                if not is_valid_totp and not is_valid_backup:
                    record_attempt(ip)
                    user.failed_login_attempts += 1
                    if user.failed_login_attempts >= settings.auth_user_lockout_threshold:
                        user.lockout_until = time.time() + settings.auth_user_lockout_duration_seconds
                        notify_lockout(username)
                    _repos().user_repo.save(user)

                    now = time.time()
                    key = (username or "unknown", ip or "unknown")
                    last_ts = MFA_WARN_LAST.get(key, 0)
                    if now - last_ts > 60:
                        _log().warning("Invalid MFA token for user: %s", username)
                        MFA_WARN_LAST[key] = now
                    else:
                        _log().debug("Invalid MFA token (suppressed, rate-limited) for user: %s", username)
                    return api_response(status="error", message="Invalid MFA token", code=401)

            _repos().login_attempt_repo.delete_by_ip(ip)
            user.failed_login_attempts = 0
            user.lockout_until = None
            _repos().user_repo.save(user)

            payload = {
                "sub": username,
                "role": user.role,
                "mfa_enabled": user.mfa_enabled,
                "iat": int(time.time()),
                "exp": int(time.time()) + settings.auth_access_token_ttl_seconds,
            }
            token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
            refresh_token = secrets.token_urlsafe(64)
            _repos().refresh_token_repo.save(
                RefreshTokenDB(
                    token=refresh_token, username=username, expires_at=time.time() + settings.auth_refresh_token_ttl_seconds
                )
            )

            _log().info("User login successful: %s", username)
            log_audit("login_success", {"username": username})
            return api_response(
                data={
                    "access_token": token,
                    "refresh_token": refresh_token,
                    "username": username,
                    "role": user.role,
                    "mfa_required": user.mfa_enabled,
                }
            )

        record_attempt(ip)
        if user:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= settings.auth_user_lockout_threshold:
                user.lockout_until = time.time() + settings.auth_user_lockout_duration_seconds
                notify_lockout(username)
            _repos().user_repo.save(user)

        _log().warning("Failed login attempt for user: %s from %s", username, ip)
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
            _log().warning("Rate limit exceeded for refresh-token attempts from %s", ip)
            return api_response(status="error", message="Too many login attempts. Please try again later.", code=429)

        data = request.json
        refresh_token = data.get("refresh_token")

        if not refresh_token:
            record_attempt(ip)
            return api_response(status="error", message="Missing refresh token", code=400)

        token_obj = _repos().refresh_token_repo.get_by_token(refresh_token)

        if not token_obj or token_obj.expires_at < time.time():
            record_attempt(ip)
            if token_obj:
                _repos().refresh_token_repo.delete(refresh_token)
            return api_response(status="error", message="Invalid or expired refresh token", code=401)

        username = token_obj.username
        user = _repos().user_repo.get_by_username(username)

        if not user:
            return api_response(status="error", message="User no longer exists", code=401)

        payload = {
            "sub": username,
            "role": user.role,
            "mfa_enabled": user.mfa_enabled,
            "iat": int(time.time()),
            "exp": int(time.time()) + settings.auth_access_token_ttl_seconds,
        }
        new_token = jwt.encode(payload, settings.secret_key, algorithm="HS256")

        _repos().refresh_token_repo.delete(refresh_token)
        new_refresh_token = secrets.token_urlsafe(64)
        _repos().refresh_token_repo.save(
            RefreshTokenDB(
                token=new_refresh_token, username=username, expires_at=time.time() + settings.auth_refresh_token_ttl_seconds
            )
        )

        return api_response(
            data={"access_token": new_token, "refresh_token": new_refresh_token, "username": username, "role": user.role}
        )

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
        user = _repos().user_repo.get_by_username(username)
        if not user:
            return api_response(status="error", message="User not found", code=404)

        return api_response(data={"username": user.username, "role": user.role, "mfa_enabled": user.mfa_enabled})

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
        user = _repos().user_repo.get_by_username(username)

        if not user or not check_password_hash(user.password_hash, old_password):
            return api_response(status="error", message="Invalid old password", code=401)

        if check_password_history(username, new_password):
            return api_response(status="error", message="You cannot reuse your last 3 passwords.", code=400)

        _repos().password_history_repo.save(PasswordHistoryDB(username=username, password_hash=user.password_hash))
        user.password_hash = generate_password_hash(new_password)
        _repos().user_repo.save(user)
        _repos().refresh_token_repo.delete_by_username(username)

        _log().info("Password changed for user: %s", username)
        log_audit("password_changed", {"target_user": username})
        return api_response(data={"status": "password_changed"})
