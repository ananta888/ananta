"""SPLIT-037: Admin user-management routes (/users, /users/<name>, /users/<name>/...).

Mounted onto the shared ``auth_bp`` blueprint via :func:`register_routes`.
Behavior preserved 1:1 from the original auth.py (lines 663-892).
"""
from __future__ import annotations

from flask import request
from werkzeug.security import generate_password_hash

from agent.auth import admin_required
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.db_models import PasswordHistoryDB, UserDB
from agent.routes import auth as _auth_shim
from agent.routes._auth_password import (
    check_password_history,
    validate_password_complexity,
)

_log = _auth_shim._log
_repos = _auth_shim._repos


def register_routes(auth_bp) -> None:
    """Attach the admin user-management routes to the auth blueprint."""

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
        users = _repos().user_repo.get_all()
        safe_users = []
        for u in users:
            safe_users.append({"username": u.username, "role": u.role, "mfa_enabled": u.mfa_enabled})
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

        if _repos().user_repo.get_by_username(username):
            return api_response(status="error", message="User already exists", code=400)

        _repos().user_repo.save(UserDB(username=username, password_hash=generate_password_hash(password), role=role))

        _log().info("User created by admin: %s (role: %s)", username, role)
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

        # Reihenfolge ist relevant: abhaengige Datensaetze zuerst loeschen.
        _repos().password_history_repo.delete_by_username(username)
        _repos().refresh_token_repo.delete_by_username(username)

        if not _repos().user_repo.delete(username):
            return api_response(status="error", message="User not found", code=404)

        _log().info("User deleted by admin: %s", username)
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

        user = _repos().user_repo.get_by_username(username)
        if not user:
            return api_response(status="error", message="User not found", code=404)

        if check_password_history(username, new_password):
            return api_response(status="error", message="User cannot reuse their last 3 passwords.", code=400)

        _repos().password_history_repo.save(PasswordHistoryDB(username=username, password_hash=user.password_hash))

        user.password_hash = generate_password_hash(new_password)
        _repos().user_repo.save(user)
        _repos().refresh_token_repo.delete_by_username(username)

        _log().info("Password reset by admin for user: %s", username)
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

        user = _repos().user_repo.get_by_username(username)
        if not user:
            return api_response(status="error", message="User not found", code=404)

        user.role = role
        _repos().user_repo.save(user)

        _log().info("Role updated by admin for user %s: %s", username, role)
        log_audit("user_role_updated", {"target_user": username, "new_role": role})
        return api_response(data={"status": "role_updated", "username": username, "role": role})
