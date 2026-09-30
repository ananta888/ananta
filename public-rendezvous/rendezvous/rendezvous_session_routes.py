"""Session, membership and key-exchange endpoints of the public rendezvous app.

``app.create_app`` owns the Flask application; it registers these views
through :meth:`SessionRoutes.register` and injects the request helpers, the
rendezvous service and the configuration (SRP/DIP). View function names - and
therefore Flask endpoint names and URLs - are unchanged.
"""

from __future__ import annotations

import logging
import math
import uuid
from typing import Any

from flask import Flask, jsonify, request


class SessionRoutes:
    """Rendezvous session views bound to their collaborators."""

    def __init__(self, *, support: Any, service: Any, config: Any, logger: logging.Logger) -> None:
        self._support = support
        self._service = service
        self._config = config
        self._log = logger

    def register(self, app: Flask) -> None:
        """Add the views with their original URLs, methods and endpoint names."""
        app.add_url_rule(
            "/rendezvous/sessions",
            endpoint="create_session",
            view_func=self.create_session,
            methods=["POST"],
        )
        app.add_url_rule(
            "/rendezvous/sessions",
            endpoint="list_sessions",
            view_func=self.list_sessions,
            methods=["GET"],
        )
        app.add_url_rule(
            "/rendezvous/sessions/catalog",
            endpoint="list_sessions_by_membership_proof",
            view_func=self.list_sessions_by_membership_proof,
            methods=["POST"],
        )
        app.add_url_rule(
            "/rendezvous/sessions/join",
            endpoint="join_session_by_invite",
            view_func=self.join_session_by_invite,
            methods=["POST"],
        )
        app.add_url_rule(
            "/rendezvous/sessions/<session_id>/join",
            endpoint="join_session",
            view_func=self.join_session,
            methods=["POST"],
        )
        app.add_url_rule(
            "/rendezvous/sessions/<session_id>/participants",
            endpoint="list_participants",
            view_func=self.list_participants,
            methods=["GET"],
        )
        app.add_url_rule(
            "/rendezvous/sessions/<session_id>/security/key-packages",
            endpoint="key_packages",
            view_func=self.key_packages,
            methods=["GET"],
        )
        app.add_url_rule(
            "/rendezvous/sessions/<session_id>/security/key-confirmations",
            endpoint="put_key_confirmation",
            view_func=self.put_key_confirmation,
            methods=["POST"],
        )
        app.add_url_rule(
            "/rendezvous/sessions/<session_id>/security/key-confirmations",
            endpoint="get_key_confirmation",
            view_func=self.get_key_confirmation,
            methods=["GET"],
        )
        app.add_url_rule(
            "/rendezvous/sessions/<session_id>/permissions",
            endpoint="update_permissions",
            view_func=self.update_permissions,
            methods=["PATCH"],
        )
        app.add_url_rule(
            "/rendezvous/sessions/<session_id>",
            endpoint="revoke_session",
            view_func=self.revoke_session,
            methods=["DELETE"],
        )
        app.add_url_rule(
            "/rendezvous/sessions/<session_id>/membership",
            endpoint="leave_session",
            view_func=self.leave_session,
            methods=["DELETE"],
        )
        app.add_url_rule(
            "/rendezvous/sessions/<session_id>/membership/runtime",
            endpoint="set_membership_runtime",
            view_func=self.set_membership_runtime,
            methods=["PUT"],
        )

    def create_session(self):
        ctx = self._support.require_auth()
        if not ctx:
            return self._support.auth_error()
        body, body_error = self._support.closed_json_body(
            {
                "title",
                "permissions",
                "allowed_permissions",
                "permissions_version",
                "security_contract_version",
                "security_mode",
                "public_key_spki_b64",
                "public_key_fingerprint",
                "mode",
                "transport",
                "expires_at",
                "owner_device_id",
                "owner_device_fingerprint",
                "identity_binding_version",
                "public_media_e2ee_version",
                "public_media_capabilities",
            }
        )
        if body_error:
            return body_error
        assert body is not None
        if (
            body.get("security_mode") not in {None, "strict_e2ee"}
            or body.get("security_contract_version") not in {None, 1}
            or body.get("mode") not in {None, "p2p"}
            or body.get("transport") not in {None, "webrtc"}
        ):
            return jsonify({"error": "strict_e2ee_required"}), 400
        identity_binding_version = body.get("identity_binding_version", 1)
        if (
            isinstance(identity_binding_version, bool)
            or not isinstance(identity_binding_version, int)
            or identity_binding_version not in {1, 2}
        ):
            return jsonify({"error": "identity_binding_version_unsupported"}), 400
        try:
            public_media_e2ee_version = self._service.normalize_public_media_advertisement(
                body.get("public_media_e2ee_version"),
                body.get("public_media_capabilities"),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if public_media_e2ee_version and identity_binding_version != 2:
            return jsonify({"error": "public_media_identity_binding_v2_required"}), 400
        device_fp = str(body.get("owner_device_fingerprint") or "").strip()
        device_id = str(body.get("owner_device_id") or "").strip()
        public_key = str(body.get("public_key_spki_b64") or "").strip()
        if not device_id or not device_fp or not public_key:
            return jsonify({"error": "device_identity_required"}), 400
        membership_capability = self._support.membership_capability()
        is_recovery = False
        if identity_binding_version == 2:
            if limited := self._support.recovery_probe_limit(ctx.account_id):
                return limited
            is_recovery = self._service.is_owner_create_recovery(
                account_id=ctx.account_id,
                device_fingerprint=device_fp,
                membership_capability=membership_capability,
                public_media_e2ee_version=public_media_e2ee_version,
            )
        if not is_recovery:
            if limited := self._support.rate_limit_guard(
                "create", ctx.account_id, self._config.RATE_CREATE_LIMIT, self._config.RATE_CREATE_WINDOW,
            ):
                return limited
        requested_expires_at = body.get("expires_at")
        if requested_expires_at is not None and (
            isinstance(requested_expires_at, bool)
            or not isinstance(requested_expires_at, (int, float))
            or not math.isfinite(float(requested_expires_at))
        ):
            return jsonify({"error": "session_expiry_invalid"}), 400
        try:
            session = self._service.create_session(
                owner_user_id=ctx.peer_id,
                owner_user_sub=ctx.sub,
                owner_device_fingerprint=device_fp,
                owner_device_id=device_id,
                owner_public_key_spki_b64=public_key,
                oidc_issuer=ctx.issuer,
                allowed_permissions=body.get("allowed_permissions") or body.get("permissions"),
                title=str(body.get("title") or "Rendezvous Session"),
                requested_expires_at=requested_expires_at,
                identity_binding_version=identity_binding_version,
                membership_capability=membership_capability,
                public_media_e2ee_version=public_media_e2ee_version,
                public_media_capabilities=body.get("public_media_capabilities"),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        idempotent = bool(session.pop("_idempotent", False))
        local_peer_id = str(session.get("owner_peer_id") or "") if identity_binding_version == 2 else ctx.account_id
        local_session = self._support.session_for_local_peer(
            session,
            local_peer_id,
            role="owner",
            runtime_state=str(session.get("local_runtime_state") or "active"),
        )
        self._log.info("session_created id=%s identity_binding_version=%d", session["id"], identity_binding_version)
        response = jsonify(
            {
                "ok": True,
                "local_peer_id": local_peer_id,
                "session": local_session,
                "data": local_session,
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response, 200 if idempotent else 201

    def list_sessions(self):
        ctx = self._support.require_auth()
        if not ctx:
            return self._support.auth_error()
        sessions = self._service.list_sessions_for_user(requester_user_id=ctx.account_id)
        local_peer_id = ctx.account_id
        response = jsonify(
            {
                "ok": True,
                "local_peer_id": local_peer_id,
                "data": {"items": sessions, "local_peer_id": local_peer_id},
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response, 200

    def list_sessions_by_membership_proof(self):
        """List V2 sessions only after exact per-session capability validation."""
        ctx = self._support.require_auth()
        if not ctx:
            return self._support.auth_error()
        body, body_error = self._support.closed_json_body({"memberships"})
        if body_error:
            return body_error
        assert body is not None
        raw_memberships = body.get("memberships")
        if not isinstance(raw_memberships, list) or len(raw_memberships) > 32:
            return jsonify({"error": "catalog_request_invalid"}), 400
        proofs: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for raw_proof in raw_memberships:
            if not isinstance(raw_proof, dict) or set(raw_proof) != {
                "session_id",
                "local_peer_id",
                "membership_capability",
            }:
                return jsonify({"error": "catalog_request_invalid"}), 400
            session_id = raw_proof.get("session_id")
            local_peer_id = raw_proof.get("local_peer_id")
            capability = raw_proof.get("membership_capability")
            if not all(isinstance(value, str) for value in (session_id, local_peer_id, capability)):
                return jsonify({"error": "catalog_request_invalid"}), 400
            try:
                normalized_session_id = str(uuid.UUID(session_id))
            except (AttributeError, ValueError):
                return jsonify({"error": "catalog_request_invalid"}), 400
            if (
                not self._service.is_device_peer_id(local_peer_id)
                or not self._service.is_membership_capability(capability)
                or (normalized_session_id, local_peer_id) in seen
            ):
                return jsonify({"error": "catalog_request_invalid"}), 400
            seen.add((normalized_session_id, local_peer_id))
            proofs.append(
                {
                    "session_id": normalized_session_id,
                    "local_peer_id": local_peer_id,
                    "membership_capability": capability,
                }
            )
        if limited := self._support.membership_probe_limit(ctx.account_id):
            return limited
        sessions = self._service.list_sessions_for_membership_proofs(
            requester_user_id=ctx.account_id,
            membership_proofs=proofs,
        )
        response = jsonify({"ok": True, "data": {"items": sessions}})
        response.headers["Cache-Control"] = "no-store"
        return response, 200

    def join_session_by_invite(self):
        return self._join_by_invite(expected_session_id="")

    def join_session(self, session_id: str):
        return self._join_by_invite(expected_session_id=session_id)

    def _join_by_invite(self, *, expected_session_id: str):
        ctx = self._support.require_auth()
        if not ctx:
            return self._support.auth_error()
        body, body_error = self._support.closed_json_body(
            {
                "invite_code",
                "minimum_security_mode",
                "public_key_spki_b64",
                "public_key_fingerprint",
                "device_id",
                "device_fingerprint",
                "identity_binding_version",
                "public_media_e2ee_version",
                "public_media_capabilities",
            }
        )
        if body_error:
            return body_error
        assert body is not None
        if body.get("minimum_security_mode") not in {None, "strict_e2ee"}:
            return jsonify({"error": "strict_e2ee_required"}), 400
        expected_identity_binding_version = body.get("identity_binding_version")
        if expected_identity_binding_version is not None and (
            isinstance(expected_identity_binding_version, bool)
            or not isinstance(expected_identity_binding_version, int)
            or expected_identity_binding_version not in {1, 2}
        ):
            return jsonify({"error": "identity_binding_version_unsupported"}), 400
        try:
            public_media_e2ee_version = self._service.normalize_public_media_advertisement(
                body.get("public_media_e2ee_version"),
                body.get("public_media_capabilities"),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if public_media_e2ee_version and expected_identity_binding_version != 2:
            return jsonify({"error": "public_media_identity_binding_v2_required"}), 400
        invite_code = str(body.get("invite_code") or "").strip()
        membership_capability = self._support.membership_capability()
        is_recovery = False
        if expected_identity_binding_version == 2:
            if limited := self._support.recovery_probe_limit(ctx.account_id):
                return limited
            is_recovery = self._service.is_join_recovery(
                invite_code=invite_code,
                account_id=ctx.account_id,
                device_fingerprint=str(body.get("device_fingerprint") or "").strip(),
                membership_capability=membership_capability,
                public_media_e2ee_version=public_media_e2ee_version,
            )
        # OIDC account identity, never forwarding headers, owns the abuse bucket.
        if not is_recovery:
            if limited := self._support.rate_limit_guard(
                "join_peer", ctx.account_id, self._config.RATE_JOIN_LIMIT, self._config.RATE_JOIN_WINDOW,
            ):
                return limited
        if not invite_code:
            return jsonify({"error": "invite_code_required"}), 400
        result = self._service.join_session(
            invite_code=invite_code,
            user_id=ctx.peer_id,
            user_sub=ctx.sub,
            device_id=str(body.get("device_id") or "").strip(),
            device_fingerprint=str(body.get("device_fingerprint") or "").strip(),
            public_key_spki_b64=str(body.get("public_key_spki_b64") or "").strip(),
            oidc_issuer=ctx.issuer,
            expected_session_id=expected_session_id,
            membership_capability=membership_capability,
            expected_identity_binding_version=expected_identity_binding_version,
            public_media_e2ee_version=public_media_e2ee_version,
            public_media_capabilities=body.get("public_media_capabilities"),
        )
        if not result.get("ok"):
            reason = result["reason"]
            status = (
                404
                if reason == "session_not_found"
                else 403
                if reason in {"session_revoked", "session_expired", "oidc_issuer_mismatch", "forbidden"}
                else 400
            )
            return jsonify({"error": reason}), status
        session_label = expected_session_id or "invite"
        participant = result.get("participant") or {}
        local_peer_id = str(participant.get("peer_id") or participant.get("user_id") or "")
        local_session = self._support.session_for_local_peer(
            result.get("session"),
            local_peer_id,
            role="participant",
            runtime_state=str((result.get("session") or {}).get("local_runtime_state") or "active"),
        )
        self._log.info(
            "participant_joined session=%s identity_binding_version=%s",
            session_label,
            local_session.get("identity_binding_version"),
        )
        response = jsonify(
            {
                "ok": True,
                "local_peer_id": local_peer_id,
                "participant": participant,
                "session": local_session,
                "data": local_session,
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response, 201 if not result.get("idempotent") else 200

    def list_participants(self, session_id: str):
        ctx = self._support.require_auth()
        if not ctx:
            return self._support.auth_error()
        requested_peer_id = self._support.requested_peer_id()
        capability = self._support.membership_capability()
        result = self._service.get_participants(
            session_id=session_id,
            requester_user_id=ctx.account_id,
            requester_peer_id=requested_peer_id,
            membership_capability=capability,
        )
        if not result.get("ok"):
            reason = result["reason"]
            status = self._support.member_error_status(reason)
            return jsonify({"error": reason}), status
        touched = self._service.touch_participant(
            session_id=session_id,
            user_id=ctx.account_id,
            requester_peer_id=requested_peer_id,
            membership_capability=capability,
        )
        if not touched.get("ok"):
            reason = str(touched.get("reason") or "forbidden")
            return jsonify({"error": reason}), self._support.member_error_status(reason)
        local_peer_id = str(result["local_peer_id"])
        return jsonify(
            {
                "ok": True,
                "local_peer_id": local_peer_id,
                "data": {"participants": result["participants"], "local_peer_id": local_peer_id},
            }
        ), 200

    def key_packages(self, session_id: str):
        ctx = self._support.require_auth()
        if not ctx:
            return self._support.auth_error()
        result = self._service.get_key_packages(
            session_id=session_id,
            requester_user_id=ctx.account_id,
            requester_peer_id=self._support.requested_peer_id(),
            membership_capability=self._support.membership_capability(),
        )
        if not result.get("ok"):
            reason = result["reason"]
            status = self._support.member_error_status(reason)
            return jsonify({"error": reason}), status
        return jsonify(result), 200

    def put_key_confirmation(self, session_id: str):
        ctx = self._support.require_auth()
        if not ctx:
            return self._support.auth_error()
        body, body_error = self._support.closed_json_body(
            {
                "recipient_peer_id",
                "package_id",
                "epoch",
                "confirmation_tag",
            }
        )
        if body_error:
            return body_error
        assert body is not None
        epoch = body.get("epoch")
        if isinstance(epoch, bool) or not isinstance(epoch, int):
            return jsonify({"error": "epoch_invalid"}), 400
        result = self._service.put_key_confirmation(
            session_id=session_id,
            sender_peer_id=self._support.selected_peer_id(ctx.account_id),
            recipient_peer_id=str(body.get("recipient_peer_id") or "").strip(),
            package_id=str(body.get("package_id") or "").strip(),
            epoch=epoch,
            confirmation_tag=str(body.get("confirmation_tag") or "").strip(),
            sender_account_id=ctx.account_id,
            membership_capability=self._support.membership_capability(),
        )
        if not result.get("ok"):
            reason = result["reason"]
            return jsonify({"error": reason}), self._support.member_error_status(reason)
        local_peer_id = self._support.selected_peer_id(ctx.account_id)
        return jsonify({**result, "local_peer_id": local_peer_id}), 200 if result.get("idempotent") else 201

    def get_key_confirmation(self, session_id: str):
        ctx = self._support.require_auth()
        if not ctx:
            return self._support.auth_error()
        sender_peer_id = str(request.args.get("sender_peer_id") or "").strip()
        result = self._service.get_key_confirmation(
            session_id=session_id,
            requester_user_id=ctx.account_id,
            sender_peer_id=sender_peer_id,
            requester_peer_id=self._support.requested_peer_id(),
            membership_capability=self._support.membership_capability(),
        )
        if not result.get("ok"):
            reason = result["reason"]
            status = self._support.member_error_status(reason)
            return jsonify({"error": reason}), status
        return jsonify({**result, "local_peer_id": self._support.selected_peer_id(ctx.account_id)}), 200

    def update_permissions(self, session_id: str):
        ctx = self._support.require_auth()
        if not ctx:
            return self._support.auth_error()
        body, body_error = self._support.closed_json_body({"permissions"})
        if body_error:
            return body_error
        assert body is not None
        permissions = body.get("permissions")
        if not isinstance(permissions, dict):
            return jsonify({"error": "permissions_required"}), 400
        result = self._service.update_session_permissions(
            session_id=session_id,
            actor_user_id=ctx.account_id,
            permissions=permissions,
            actor_peer_id=self._support.requested_peer_id(),
            membership_capability=self._support.membership_capability(),
        )
        if not result.get("ok"):
            reason = result["reason"]
            if reason == "permission_update_rekey_required":
                return jsonify({"error": reason, "reason_code": reason}), 409
            return jsonify({"error": reason}), self._support.member_error_status(reason, default=404)
        local_peer_id = self._support.selected_peer_id(ctx.account_id)
        local_session = self._support.session_for_local_peer(result.get("session"), local_peer_id)
        return jsonify(
            {
                "ok": True,
                "local_peer_id": local_peer_id,
                "data": local_session,
            }
        ), 200

    def revoke_session(self, session_id: str):
        ctx = self._support.require_auth()
        if not ctx:
            return self._support.auth_error()
        result = self._service.revoke_session(
            session_id=session_id,
            actor_user_id=ctx.account_id,
            actor_peer_id=self._support.requested_peer_id(),
            membership_capability=self._support.membership_capability(),
        )
        if not result.get("ok"):
            reason = result["reason"]
            return jsonify({"error": reason}), self._support.member_error_status(reason, default=404)
        self._log.info("session_revoked id=%s", session_id)
        return jsonify({"ok": True, "local_peer_id": result["local_peer_id"]}), 200

    def leave_session(self, session_id: str):
        """Retire the caller's exact guest membership; owners end the session."""
        ctx = self._support.require_auth()
        if not ctx:
            return self._support.auth_error()
        result = self._service.leave_session(
            session_id=session_id,
            actor_user_id=ctx.account_id,
            actor_peer_id=self._support.requested_peer_id(),
            membership_capability=self._support.membership_capability(),
        )
        if not result.get("ok"):
            reason = str(result.get("reason") or "forbidden")
            return jsonify({"error": reason}), self._support.member_error_status(reason)
        response = jsonify(
            {
                "ok": True,
                "local_peer_id": result["local_peer_id"],
                "idempotent": bool(result.get("idempotent")),
            }
        )
        self._log.info(
            "participant_left session=%s idempotent=%s",
            session_id,
            bool(result.get("idempotent")),
        )
        response.headers["Cache-Control"] = "no-store"
        return response, 200

    def set_membership_runtime(self, session_id: str):
        """Activate or park one exact v2 membership without retiring it."""
        ctx = self._support.require_auth()
        if not ctx:
            return self._support.auth_error()
        body, body_error = self._support.closed_json_body({"state"})
        if body_error:
            return body_error
        assert body is not None
        state = body.get("state")
        if state not in {"active", "parked"}:
            return jsonify({"error": "runtime_state_invalid"}), 400
        if limited := self._support.membership_probe_limit(ctx.account_id):
            return limited
        result = self._service.set_membership_runtime(
            session_id=session_id,
            account_id=ctx.account_id,
            requested_peer_id=self._support.requested_peer_id(),
            membership_capability=self._support.membership_capability(),
            state=state,
        )
        if not result.get("ok"):
            reason = str(result.get("reason") or "membership_state_conflict")
            status = (
                400
                if reason == "runtime_state_invalid"
                else self._support.member_error_status(reason)
            )
            return jsonify({"error": reason}), status
        local_peer_id = str(result["local_peer_id"])
        response = jsonify(
            {
                "ok": True,
                "local_peer_id": local_peer_id,
                "data": {
                    "state": result["state"],
                    "security_epoch": result["security_epoch"],
                    "changed": bool(result["changed"]),
                    "parked_session_ids": list(result["parked_session_ids"]),
                },
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response, 200


__all__ = ["SessionRoutes"]
