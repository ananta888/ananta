"""Creation and invite-based joining of strict Pair sessions.

Owns session admission (create, join and their idempotent recovery probes).
Infrastructure - connection, schema, cleanup, clock and configuration - comes
from the injected :class:`RendezvousRuntime` (SRP/DIP).
"""

from __future__ import annotations

import json
import math
import secrets
import sqlite3
import uuid
from typing import Any

from pair_security import (
    normalize_public_media_advertisement,
    public_media_capabilities_for_version,
    spki_fingerprint,
)
from peer_identity import (
    canonical_device_peer_id,
    is_canonical_account_id,
    is_membership_capability,
    membership_capability_digest,
    owner_capability_lookup_digest,
)
from rendezvous_membership import activate_joined_v2_membership_locked, set_membership_runtime_locked
from rendezvous_records import (
    get_session_by_id,
    is_protocol_identifier,
    new_invite_code,
    owner_create_request_digest,
    parse_permissions,
    row_to_session,
    session_memberships,
    session_snapshot,
    subject_hash,
)
from rendezvous_runtime import RendezvousRuntime


class SessionAdmission:
    """Create sessions and admit invited devices under the strict Pair contract."""

    def __init__(self, *, runtime: RendezvousRuntime) -> None:
        self._runtime = runtime

    def create_session(
        self,
        *,
        owner_user_id: str,
        owner_user_sub: str,
        owner_device_fingerprint: str,
        owner_device_id: str = "",
        owner_public_key_spki_b64: str = "",
        oidc_issuer: str,
        allowed_permissions: dict[str, bool] | None = None,
        title: str = "Rendezvous Session",
        requested_expires_at: float | None = None,
        identity_binding_version: int = 1,
        membership_capability: str = "",
        public_media_e2ee_version: int | None = None,
        public_media_capabilities: Any = None,
    ) -> dict[str, Any]:
        self._runtime.ensure_schema()
        self._runtime.cleanup_expired()
        owner_device_id = str(owner_device_id or "").strip()
        owner_device_fingerprint = str(owner_device_fingerprint or "").strip().lower()
        owner_public_key_spki_b64 = str(owner_public_key_spki_b64 or "").strip()
        oidc_issuer = str(oidc_issuer or "").strip()
        owner_user_sub = str(owner_user_sub or "").strip()
        if not is_canonical_account_id(owner_user_id) or not owner_user_sub or not oidc_issuer:
            raise ValueError("oidc_identity_required")
        if identity_binding_version not in {1, 2}:
            raise ValueError("identity_binding_version_unsupported")
        normalized_media_version = normalize_public_media_advertisement(
            public_media_e2ee_version,
            public_media_capabilities,
        )
        if normalized_media_version and identity_binding_version != 2:
            raise ValueError("public_media_identity_binding_v2_required")
        membership_capability = str(membership_capability or "").strip()
        if identity_binding_version == 2:
            if not membership_capability:
                raise ValueError("membership_capability_required")
            if not is_membership_capability(membership_capability):
                raise ValueError("membership_capability_invalid")
        if not owner_device_id or not owner_device_fingerprint or not owner_public_key_spki_b64:
            raise ValueError("device_identity_required")
        if not is_protocol_identifier(owner_device_id):
            raise ValueError("device_identity_invalid")
        if not secrets.compare_digest(spki_fingerprint(owner_public_key_spki_b64), owner_device_fingerprint):
            raise ValueError("device_key_substitution")
        owner_peer_id = (
            canonical_device_peer_id(owner_user_id, owner_device_fingerprint)
            if identity_binding_version == 2
            else owner_user_id
        )
        perms = {
            "chat": True,
            "view_tui": False,
            "remote_cursor": False,
            "artifact_share": False,
            "remote_control": False,
        }
        if isinstance(allowed_permissions, dict):
            for key in perms:
                if key in allowed_permissions:
                    perms[key] = bool(allowed_permissions[key])
        perms["remote_control"] = False

        now = self._runtime.clock()
        if requested_expires_at is None:
            expires_at = now + self._runtime.config.SESSION_MAX_DURATION_SECONDS
        elif (
            isinstance(requested_expires_at, bool)
            or not isinstance(requested_expires_at, (int, float))
            or not math.isfinite(float(requested_expires_at))
            or float(requested_expires_at) <= now
        ):
            raise ValueError("session_expiry_invalid")
        else:
            expires_at = min(
                float(requested_expires_at),
                now + self._runtime.config.SESSION_MAX_DURATION_SECONDS,
            )

        normalized_title = str(title or "Rendezvous Session")[:120]
        owner_subject_hash = subject_hash(oidc_issuer, owner_user_sub)
        create_request_hash = (
            owner_create_request_digest(
                account_id=owner_user_id,
                peer_id=owner_peer_id,
                subject_hash=owner_subject_hash,
                device_id=owner_device_id,
                device_fingerprint=owner_device_fingerprint,
                public_key_spki_b64=owner_public_key_spki_b64,
                oidc_issuer=oidc_issuer,
                title=normalized_title,
                permissions=perms,
                requested_expires_at=(float(requested_expires_at) if requested_expires_at is not None else None),
                public_media_e2ee_version=normalized_media_version,
            )
            if identity_binding_version == 2
            else ""
        )
        sid = str(uuid.uuid4())
        membership_capability = membership_capability if identity_binding_version == 2 else ""
        capability_lookup_hash = (
            owner_capability_lookup_digest(owner_user_id, owner_peer_id, membership_capability)
            if membership_capability
            else ""
        )
        capability_hash = (
            membership_capability_digest(sid, owner_user_id, owner_peer_id, membership_capability)
            if membership_capability
            else ""
        )
        with self._runtime.connect() as conn:
            while True:
                code = new_invite_code()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    if capability_lookup_hash:
                        retired = conn.execute(
                            """SELECT 1 FROM retired_owner_capabilities
                               WHERE owner_capability_lookup_hash = ? LIMIT 1""",
                            (capability_lookup_hash,),
                        ).fetchone()
                        if retired:
                            conn.execute("ROLLBACK")
                            raise ValueError("membership_capability_retired")
                        existing_row = conn.execute(
                            "SELECT * FROM sessions WHERE owner_capability_lookup_hash = ? LIMIT 1",
                            (capability_lookup_hash,),
                        ).fetchone()
                        if existing_row:
                            existing = row_to_session(existing_row)
                            if (
                                existing.get("revoked_at") is not None
                                or float(existing.get("expires_at") or 0) <= self._runtime.clock()
                            ):
                                conn.execute("ROLLBACK")
                                raise ValueError("membership_capability_retired")
                            expected_hash = membership_capability_digest(
                                str(existing["id"]),
                                owner_user_id,
                                owner_peer_id,
                                membership_capability,
                            )
                            immutable_matches = (
                                secrets.compare_digest(
                                    str(existing.get("_owner_create_request_hash") or ""),
                                    create_request_hash,
                                )
                                and int(existing.get("identity_binding_version") or 0) == 2
                                and int(existing.get("public_media_e2ee_version") or 0) == normalized_media_version
                                and secrets.compare_digest(
                                    str(existing.get("_owner_membership_capability_hash") or ""),
                                    expected_hash,
                                )
                            )
                            if not immutable_matches:
                                conn.execute("ROLLBACK")
                                raise ValueError("membership_capability_conflict")
                            owner_member = session_memberships(conn, existing)[0]
                            set_membership_runtime_locked(
                                conn, existing, owner_member, "active", clock=self._runtime.clock
                            )
                            existing = get_session_by_id(conn, str(existing["id"])) or existing
                            conn.execute("COMMIT")
                            snapshot = session_snapshot(conn, existing, include_participants=True)
                            snapshot["local_role"] = "owner"
                            snapshot["local_runtime_state"] = "active"
                            snapshot["_idempotent"] = True
                            return snapshot
                    conn.execute(
                        """
                        INSERT INTO sessions (
                            id, owner_user_id, owner_user_sub_hash, owner_device_fingerprint, oidc_issuer,
                            title, invite_code, allowed_permissions, expires_at, created_at, revoked_at,
                            owner_device_id, owner_public_key_spki_b64, security_epoch,
                            security_mode, security_contract_version, identity_binding_version,
                            owner_account_id, owner_peer_id, owner_membership_capability_hash,
                            owner_capability_lookup_hash, owner_create_request_hash,
                            public_media_e2ee_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sid,
                            owner_user_id,
                            owner_subject_hash,
                            owner_device_fingerprint,
                            oidc_issuer,
                            normalized_title,
                            code,
                            json.dumps(perms, separators=(",", ":")),
                            expires_at,
                            now,
                            owner_device_id,
                            owner_public_key_spki_b64,
                            "strict_e2ee",
                            1,
                            identity_binding_version,
                            owner_user_id,
                            owner_peer_id,
                            capability_hash,
                            capability_lookup_hash,
                            create_request_hash,
                            normalized_media_version,
                        ),
                    )
                    if identity_binding_version == 2:
                        inserted = get_session_by_id(conn, sid)
                        if not inserted:
                            raise RuntimeError("created session not found")
                        owner_member = session_memberships(conn, inserted)[0]
                        set_membership_runtime_locked(conn, inserted, owner_member, "active", clock=self._runtime.clock)
                    conn.execute("COMMIT")
                    break
                except sqlite3.IntegrityError as exc:
                    conn.execute("ROLLBACK")
                    if "sessions.invite_code" in str(exc):
                        continue
                    if capability_lookup_hash:
                        raced = conn.execute(
                            "SELECT 1 FROM sessions WHERE owner_capability_lookup_hash = ? LIMIT 1",
                            (capability_lookup_hash,),
                        ).fetchone()
                        if raced:
                            # Re-enter the locked lookup path, which verifies the
                            # full capability and immutable request digest.
                            continue
                    raise
            session = get_session_by_id(conn, sid)
            if not session:
                raise RuntimeError("created session not found")
            snapshot = session_snapshot(conn, session, include_participants=True)
            snapshot["local_role"] = "owner"
            snapshot["local_runtime_state"] = "active"
            return snapshot

    def is_owner_create_recovery(
        self,
        *,
        account_id: str,
        device_fingerprint: str,
        membership_capability: str,
        public_media_e2ee_version: int = 0,
    ) -> bool:
        """Recognize a v2 create retry so rate limiting does not orphan it."""
        try:
            peer_id = canonical_device_peer_id(account_id, device_fingerprint)
            lookup_hash = owner_capability_lookup_digest(
                account_id,
                peer_id,
                membership_capability,
            )
        except ValueError:
            return False
        self._runtime.ensure_schema()
        with self._runtime.connect() as conn:
            row = conn.execute(
                """SELECT id, owner_membership_capability_hash, public_media_e2ee_version
                   FROM sessions
                   WHERE owner_capability_lookup_hash = ?
                     AND owner_account_id = ?
                     AND owner_peer_id = ?
                     AND revoked_at IS NULL
                     AND expires_at > ?
                   LIMIT 1""",
                (lookup_hash, account_id, peer_id, self._runtime.clock()),
            ).fetchone()
            if not row:
                return False
            if int(row["public_media_e2ee_version"] or 0) != public_media_e2ee_version:
                return False
            expected_hash = membership_capability_digest(
                str(row["id"]),
                account_id,
                peer_id,
                membership_capability,
            )
            return secrets.compare_digest(
                str(row["owner_membership_capability_hash"] or ""),
                expected_hash,
            )

    def is_join_recovery(
        self,
        *,
        invite_code: str,
        account_id: str,
        device_fingerprint: str,
        membership_capability: str,
        public_media_e2ee_version: int = 0,
    ) -> bool:
        """Recognize an already committed v2 join using its original capability."""
        try:
            peer_id = canonical_device_peer_id(account_id, device_fingerprint)
        except ValueError:
            return False
        self._runtime.ensure_schema()
        with self._runtime.connect() as conn:
            row = conn.execute(
                """SELECT p.session_id, p.membership_capability_hash,
                          p.public_media_e2ee_version
                   FROM participants p
                   JOIN sessions s ON s.id = p.session_id
                   WHERE s.invite_code = ?
                     AND s.identity_binding_version = 2
                     AND s.revoked_at IS NULL
                     AND s.expires_at > ?
                     AND p.account_id = ?
                     AND p.peer_id = ?
                     AND p.revoked_at IS NULL
                   LIMIT 1""",
                (str(invite_code or "").strip().upper(), self._runtime.clock(), account_id, peer_id),
            ).fetchone()
            if not row:
                return False
            if int(row["public_media_e2ee_version"] or 0) != public_media_e2ee_version:
                return False
            try:
                expected_hash = membership_capability_digest(
                    str(row["session_id"]),
                    account_id,
                    peer_id,
                    membership_capability,
                )
            except ValueError:
                return False
            return secrets.compare_digest(
                str(row["membership_capability_hash"] or ""),
                expected_hash,
            )

    def join_session(
        self,
        *,
        invite_code: str,
        user_id: str,
        user_sub: str,
        device_id: str,
        device_fingerprint: str,
        public_key_spki_b64: str = "",
        oidc_issuer: str,
        expected_session_id: str = "",
        membership_capability: str = "",
        expected_identity_binding_version: int | None = None,
        public_media_e2ee_version: int | None = None,
        public_media_capabilities: Any = None,
    ) -> dict[str, Any]:
        self._runtime.ensure_schema()
        self._runtime.cleanup_expired()
        code = str(invite_code or "").strip().upper()
        oidc_issuer = str(oidc_issuer or "").strip()
        user_sub = str(user_sub or "").strip()
        device_id = str(device_id or "").strip()
        device_fingerprint = str(device_fingerprint or "").strip().lower()
        public_key_spki_b64 = str(public_key_spki_b64 or "").strip()
        membership_capability = str(membership_capability or "").strip()
        negotiated_identity_binding_version = (
            1 if expected_identity_binding_version is None else expected_identity_binding_version
        )
        try:
            normalized_media_version = normalize_public_media_advertisement(
                public_media_e2ee_version,
                public_media_capabilities,
            )
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        if normalized_media_version and negotiated_identity_binding_version != 2:
            return {"ok": False, "reason": "public_media_identity_binding_v2_required"}
        if not is_canonical_account_id(user_id) or not user_sub or not oidc_issuer:
            return {"ok": False, "reason": "oidc_identity_required"}
        if not device_id or not device_fingerprint or not public_key_spki_b64:
            return {"ok": False, "reason": "device_identity_required"}
        if not is_protocol_identifier(device_id):
            return {"ok": False, "reason": "device_identity_invalid"}
        try:
            actual_fingerprint = spki_fingerprint(public_key_spki_b64)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        if not secrets.compare_digest(actual_fingerprint, device_fingerprint):
            return {"ok": False, "reason": "device_key_substitution"}

        with self._runtime.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM sessions WHERE invite_code = ? AND invite_code IS NOT NULL AND invite_code != ''",
                (code,),
            ).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "invalid_invite_code"}
            session = row_to_session(row)
            sid = str(session.get("id") or "")
            if expected_session_id and sid != expected_session_id:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "session_not_found"}
            if session.get("revoked_at") is not None:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "session_revoked"}
            if float(session.get("expires_at") or 0) < self._runtime.clock():
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "session_expired"}
            sess_issuer = str(session.get("oidc_issuer") or "")
            if not sess_issuer or sess_issuer != oidc_issuer:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "oidc_issuer_mismatch"}
            identity_binding_version = int(session.get("identity_binding_version") or 0)
            if negotiated_identity_binding_version != identity_binding_version:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "identity_binding_version_mismatch"}
            if (
                session.get("security_mode") != "strict_e2ee"
                or int(session.get("security_contract_version") or 0) != 1
                or identity_binding_version not in {1, 2}
            ):
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "strict_e2ee_required"}
            peer_id = (
                canonical_device_peer_id(user_id, device_fingerprint) if identity_binding_version == 2 else user_id
            )
            owner_account_id = str(session.get("owner_account_id") or session.get("owner_user_id") or "")
            owner_peer_id = str(session.get("owner_peer_id") or session.get("owner_user_id") or "")
            if identity_binding_version == 1 and user_id == owner_account_id:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "peer_identity_must_be_distinct"}
            if secrets.compare_digest(device_fingerprint, str(session.get("owner_device_fingerprint") or "")):
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "device_key_must_be_distinct"}
            if peer_id == owner_peer_id:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "peer_identity_must_be_distinct"}

            existing = conn.execute(
                """
                SELECT id, session_id, user_id, user_sub_hash, device_id,
                       device_fingerprint, public_key_spki_b64, permissions,
                       joined_at, last_seen, revoked_at, account_id, peer_id,
                       membership_capability_hash, public_media_e2ee_version,
                       runtime_state
                FROM participants
                WHERE session_id = ?
                  AND ((? = 2 AND peer_id = ?) OR (? = 1 AND user_id = ? AND device_id = ?))
                  AND revoked_at IS NULL
                LIMIT 1
                """,
                (sid, identity_binding_version, peer_id, identity_binding_version, user_id, device_id),
            ).fetchone()
            if existing:
                expected_sub_hash = subject_hash(oidc_issuer, user_sub)
                if (
                    not secrets.compare_digest(str(existing["user_sub_hash"]), expected_sub_hash)
                    or not secrets.compare_digest(str(existing["account_id"] or existing["user_id"]), user_id)
                    or not secrets.compare_digest(str(existing["peer_id"] or existing["user_id"]), peer_id)
                    or not secrets.compare_digest(str(existing["device_id"]), device_id)
                    or not secrets.compare_digest(str(existing["device_fingerprint"]), device_fingerprint)
                    or not secrets.compare_digest(str(existing["public_key_spki_b64"]), public_key_spki_b64)
                ):
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "device_identity_conflict"}
                if identity_binding_version == 2:
                    if not membership_capability:
                        conn.execute("ROLLBACK")
                        return {"ok": False, "reason": "membership_capability_required"}
                    if not is_membership_capability(membership_capability):
                        conn.execute("ROLLBACK")
                        return {"ok": False, "reason": "membership_capability_invalid"}
                    expected_capability_hash = membership_capability_digest(
                        sid,
                        user_id,
                        peer_id,
                        membership_capability,
                    )
                    if not secrets.compare_digest(
                        str(existing["membership_capability_hash"] or ""),
                        expected_capability_hash,
                    ):
                        conn.execute("ROLLBACK")
                        return {"ok": False, "reason": "membership_capability_invalid"}
                if int(existing["public_media_e2ee_version"] or 0) != normalized_media_version:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "public_media_capability_conflict"}
                participant = {
                    "id": existing["id"],
                    "session_id": existing["session_id"],
                    "user_id": existing["user_id"],
                    "user_sub_hash": existing["user_sub_hash"],
                    "device_id": existing["device_id"],
                    "device_fingerprint": existing["device_fingerprint"],
                    "permissions": parse_permissions(existing["permissions"]),
                    "joined_at": existing["joined_at"],
                    "last_seen": existing["last_seen"],
                    "revoked_at": existing["revoked_at"],
                    "public_key_spki_b64": existing["public_key_spki_b64"],
                    "account_id": existing["account_id"] or existing["user_id"],
                    "peer_id": existing["peer_id"] or existing["user_id"],
                    "public_media_e2ee_version": int(existing["public_media_e2ee_version"] or 0),
                    "public_media_capabilities": public_media_capabilities_for_version(
                        int(existing["public_media_e2ee_version"] or 0)
                    ),
                    "runtime_state": str(existing["runtime_state"] or "active"),
                }
                session = activate_joined_v2_membership_locked(conn, session, peer_id, clock=self._runtime.clock)
                participant["runtime_state"] = "active"
                conn.execute("COMMIT")
                return {
                    "ok": True,
                    "participant": participant,
                    "session": {
                        **session_snapshot(conn, session),
                        "local_role": "participant",
                        "local_runtime_state": participant["runtime_state"],
                    },
                    "idempotent": True,
                }

            active_count = conn.execute(
                "SELECT COUNT(1) AS c FROM participants WHERE session_id = ? AND revoked_at IS NULL",
                (sid,),
            ).fetchone()
            if int(active_count["c"] or 0) >= 1:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "session_full"}

            participant = {
                "id": str(uuid.uuid4()),
                "session_id": sid,
                "user_id": user_id,
                "user_sub_hash": subject_hash(oidc_issuer, user_sub),
                "device_id": device_id,
                "device_fingerprint": device_fingerprint,
                "permissions": dict(session.get("allowed_permissions") or {}),
                "joined_at": self._runtime.clock(),
                "last_seen": self._runtime.clock(),
                "revoked_at": None,
                "public_key_spki_b64": public_key_spki_b64,
                "account_id": user_id,
                "peer_id": peer_id,
                "public_media_e2ee_version": normalized_media_version,
                "public_media_capabilities": public_media_capabilities_for_version(normalized_media_version),
                "runtime_state": "active",
            }
            if identity_binding_version == 2:
                if not membership_capability:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "membership_capability_required"}
                if not is_membership_capability(membership_capability):
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "membership_capability_invalid"}
            new_capability = membership_capability if identity_binding_version == 2 else ""
            capability_hash = (
                membership_capability_digest(sid, user_id, peer_id, new_capability) if new_capability else ""
            )
            conn.execute(
                """
                INSERT INTO participants (
                    id, session_id, user_id, user_sub_hash, device_id, device_fingerprint,
                    public_key_spki_b64, permissions, joined_at, last_seen, revoked_at,
                    account_id, peer_id, membership_capability_hash,
                    public_media_e2ee_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    participant["id"],
                    participant["session_id"],
                    participant["user_id"],
                    participant["user_sub_hash"],
                    participant["device_id"],
                    participant["device_fingerprint"],
                    participant["public_key_spki_b64"],
                    json.dumps(participant["permissions"], separators=(",", ":")),
                    participant["joined_at"],
                    participant["last_seen"],
                    participant["account_id"],
                    participant["peer_id"],
                    capability_hash,
                    normalized_media_version,
                ),
            )
            conn.execute("UPDATE sessions SET security_epoch = security_epoch + 1 WHERE id = ?", (sid,))
            conn.execute("DELETE FROM key_confirmations WHERE session_id = ?", (sid,))
            updated = get_session_by_id(conn, sid) or session
            updated = activate_joined_v2_membership_locked(conn, updated, peer_id, clock=self._runtime.clock)
            conn.execute("COMMIT")
            return {
                "ok": True,
                "participant": dict(participant),
                "session": {
                    **session_snapshot(conn, updated),
                    "local_role": "participant",
                    "local_runtime_state": "active",
                },
            }


__all__ = ["SessionAdmission"]
