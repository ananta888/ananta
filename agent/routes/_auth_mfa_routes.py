"""SPLIT-037: MFA lifecycle routes (/mfa/setup, /mfa/verify, /mfa/disable).

Mounted onto the shared ``auth_bp`` blueprint via :func:`register_routes`.
Behavior preserved 1:1 from the original auth.py (lines 486-660).
"""
from __future__ import annotations

import secrets
import time

import jwt
from flask import g, request
from werkzeug.security import generate_password_hash

from agent.auth import check_user_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.common.mfa import (
    decrypt_secret,
    encrypt_secret,
    generate_mfa_secret,
    generate_qr_code_base64,
    get_totp_uri,
    verify_totp,
)
from agent.config import settings
from agent.routes import auth as _auth_shim
from agent.routes._auth_password import (
    is_rate_limited,
    notify_lockout,
    record_attempt,
    validate_password_complexity,
)

_log = _auth_shim._log
_repos = _auth_shim._repos


def register_routes(auth_bp) -> None:
    """Attach the MFA lifecycle routes to the auth blueprint."""

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
        user = _repos().user_repo.get_by_username(username)

        if not user:
            return api_response(status="error", message="User not found", code=404)

        if user.mfa_enabled:
            return api_response(status="error", message="MFA is already enabled. Disable it first.", code=400)

        secret = generate_mfa_secret()
        user.mfa_secret = encrypt_secret(secret)
        _repos().user_repo.save(user)

        uri = get_totp_uri(username, secret)
        qr_code = generate_qr_code_base64(uri)

        return api_response(data={"secret": secret, "qr_code": qr_code})

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
            _log().warning("Rate limit exceeded for MFA verification from %s", ip)
            return api_response(status="error", message="Too many attempts. Please try again later.", code=429)

        data = request.json
        token = data.get("token")

        if not token:
            record_attempt(ip)
            return api_response(status="error", message="Missing token", code=400)

        username = g.user["sub"]
        user = _repos().user_repo.get_by_username(username)

        if not user or not user.mfa_secret:
            return api_response(status="error", message="MFA not set up", code=400)

        if verify_totp(decrypt_secret(user.mfa_secret), token):
            _repos().login_attempt_repo.delete_by_ip(ip)
            user.mfa_enabled = True
            user.failed_login_attempts = 0

            backup_codes = [secrets.token_hex(4) for _ in range(settings.auth_mfa_backup_code_count)]
            user.mfa_backup_codes = [generate_password_hash(bc) for bc in backup_codes]

            _repos().user_repo.save(user)
            log_audit("mfa_enabled", {"username": username})

            payload = {
                "sub": username,
                "role": user.role,
                "mfa_enabled": True,
                "iat": int(time.time()),
                "exp": int(time.time()) + settings.auth_access_token_ttl_seconds,
            }
            new_token = jwt.encode(payload, settings.secret_key, algorithm="HS256")

            return api_response(data={"status": "mfa_enabled", "access_token": new_token, "backup_codes": backup_codes})
        else:
            record_attempt(ip)
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= settings.auth_user_lockout_threshold:
                user.lockout_until = time.time() + settings.auth_user_lockout_duration_seconds
                notify_lockout(username)
            _repos().user_repo.save(user)
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
        user = _repos().user_repo.get_by_username(username)

        if user:
            user.mfa_enabled = False
            user.mfa_secret = None
            _repos().user_repo.save(user)
            log_audit("mfa_disabled", {"username": username})

            payload = {
                "sub": username,
                "role": user.role,
                "mfa_enabled": False,
                "iat": int(time.time()),
                "exp": int(time.time()) + settings.auth_access_token_ttl_seconds,
            }
            new_token = jwt.encode(payload, settings.secret_key, algorithm="HS256")

            return api_response(data={"status": "mfa_disabled", "access_token": new_token})

        return api_response(status="error", message="User not found", code=404)
