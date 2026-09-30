"""Authenticated operations on one session membership.

Participant presence, permission updates, leaving, revocation, runtime-state
selection and authorization checks. Infrastructure comes from the injected
:class:`RendezvousRuntime` (SRP/DIP).
"""

from __future__ import annotations

from typing import Any

from rendezvous_membership import (
    resolve_authenticated_membership,
    retired_guest_peer_for_capability,
    set_membership_runtime_locked,
    validated_strict_memberships,
)
from rendezvous_records import get_session_by_id, list_participants
from rendezvous_runtime import RendezvousRuntime


class SessionMembershipOperations:
    """Authenticate and mutate one membership of a strict Pair session."""

    def __init__(self, *, runtime: RendezvousRuntime) -> None:
        self._runtime = runtime

    def update_session_permissions(
        self,
        *,
        session_id: str,
        actor_user_id: str,
        permissions: dict[str, bool],
        actor_peer_id: str = "",
        membership_capability: str = "",
    ) -> dict[str, Any]:
        self._runtime.ensure_schema()
        with self._runtime.connect() as conn:
            session = get_session_by_id(conn, session_id)
            if not session:
                return {"ok": False, "reason": "session_not_found"}
            if session.get("revoked_at") is not None or float(session.get("expires_at") or 0) <= self._runtime.clock():
                return {"ok": False, "reason": "session_inactive"}
            try:
                resolve_authenticated_membership(
                    conn,
                    session,
                    account_id=actor_user_id,
                    requested_peer_id=actor_peer_id,
                    membership_capability=membership_capability,
                    owner_required=True,
                )
            except ValueError as exc:
                return {"ok": False, "reason": str(exc)}
            # Permission snapshots are part of the strict E2EE authorization
            # context. Updating them without advancing the security epoch and
            # negotiating fresh peer keys would leave clients with conflicting
            # authority. Public rendezvous supports only strict sessions, so the
            # mutation stays fail-closed until a rekey-capable adapter exists.
            return {"ok": False, "reason": "permission_update_rekey_required"}

    def get_participants(
        self,
        *,
        session_id: str,
        requester_user_id: str,
        requester_peer_id: str = "",
        membership_capability: str = "",
    ) -> dict[str, Any]:
        self._runtime.ensure_schema()
        with self._runtime.connect() as conn:
            session = get_session_by_id(conn, session_id)
            if not session:
                return {"ok": False, "reason": "session_not_found"}
            if session.get("revoked_at") is not None or float(session.get("expires_at") or 0) <= self._runtime.clock():
                return {"ok": False, "reason": "session_inactive"}
            try:
                requester = resolve_authenticated_membership(
                    conn,
                    session,
                    account_id=requester_user_id,
                    requested_peer_id=requester_peer_id,
                    membership_capability=membership_capability,
                )
            except ValueError as exc:
                return {"ok": False, "reason": str(exc)}

            presence = [
                {
                    "id": f"member:{session_id}:owner",
                    "user_id": session.get("owner_user_id"),
                    "account_id": session.get("owner_account_id") or session.get("owner_user_id"),
                    "peer_id": (
                        session.get("owner_peer_id")
                        if int(session.get("identity_binding_version") or 0) == 2
                        else session.get("owner_user_id")
                    ),
                    "device_id": session.get("owner_device_id"),
                    "device_fingerprint": session.get("owner_device_fingerprint"),
                    "permissions": session.get("allowed_permissions"),
                    "joined_at": session.get("created_at"),
                    "last_seen": self._runtime.clock(),
                    "last_seen_at": self._runtime.clock(),
                    "revoked_at": session.get("revoked_at"),
                    "public_media_e2ee_version": int(session.get("public_media_e2ee_version") or 0),
                    "public_media_capabilities": session.get("public_media_capabilities"),
                }
            ] + [
                {
                    "id": p.get("id"),
                    "user_id": p.get("user_id"),
                    "account_id": p.get("account_id") or p.get("user_id"),
                    "peer_id": (
                        p.get("peer_id") if int(session.get("identity_binding_version") or 0) == 2 else p.get("user_id")
                    ),
                    "device_id": p.get("device_id"),
                    "device_fingerprint": p.get("device_fingerprint"),
                    "permissions": p.get("permissions"),
                    "joined_at": p.get("joined_at"),
                    "last_seen": p.get("last_seen"),
                    "last_seen_at": p.get("last_seen"),
                    "revoked_at": p.get("revoked_at"),
                    "public_media_e2ee_version": int(p.get("public_media_e2ee_version") or 0),
                    "public_media_capabilities": p.get("public_media_capabilities"),
                }
                for p in list_participants(conn, session_id, include_revoked=True)
            ]
            return {"ok": True, "participants": presence, "local_peer_id": requester["peer_id"]}

    def touch_participant(
        self,
        *,
        session_id: str,
        user_id: str,
        requester_peer_id: str = "",
        membership_capability: str = "",
    ) -> dict[str, Any]:
        self._runtime.ensure_schema()
        with self._runtime.connect() as conn:
            session = get_session_by_id(conn, session_id)
            if not session:
                return {"ok": False, "reason": "session_not_found"}
            try:
                member = resolve_authenticated_membership(
                    conn,
                    session,
                    account_id=user_id,
                    requested_peer_id=requester_peer_id,
                    membership_capability=membership_capability,
                )
            except ValueError as exc:
                return {"ok": False, "reason": str(exc)}
            if member["owner"]:
                return {"ok": True, "local_peer_id": member["peer_id"]}
            if int(session.get("identity_binding_version") or 0) == 2:
                conn.execute(
                    """UPDATE participants SET last_seen = ?
                       WHERE session_id = ? AND peer_id = ? AND revoked_at IS NULL""",
                    (self._runtime.clock(), session_id, member["peer_id"]),
                )
            else:
                conn.execute(
                    """UPDATE participants SET last_seen = ?
                       WHERE session_id = ? AND user_id = ? AND revoked_at IS NULL""",
                    (self._runtime.clock(), session_id, user_id),
                )
            return {"ok": True, "local_peer_id": member["peer_id"]}

    def leave_session(
        self,
        *,
        session_id: str,
        actor_user_id: str,
        actor_peer_id: str = "",
        membership_capability: str = "",
    ) -> dict[str, Any]:
        """Revoke only the authenticated guest membership of a strict Pair session.

        The owner has a separate, stronger operation (``revoke_session``). A
        repeated request from the exact retired guest capability is successful so
        a lost HTTP response cannot leave the browser unsure whether it may forget
        its local membership authority.
        """
        self._runtime.ensure_schema()
        now = self._runtime.clock()
        with self._runtime.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            session = get_session_by_id(conn, session_id)
            if not session:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "session_not_found"}
            if session.get("revoked_at") is not None:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "session_revoked"}
            if float(session.get("expires_at") or 0) <= now:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "session_expired"}

            try:
                actor = resolve_authenticated_membership(
                    conn,
                    session,
                    account_id=actor_user_id,
                    requested_peer_id=actor_peer_id,
                    membership_capability=membership_capability,
                )
            except ValueError as exc:
                retired_peer_id = retired_guest_peer_for_capability(
                    conn,
                    session,
                    account_id=actor_user_id,
                    requested_peer_id=actor_peer_id,
                    membership_capability=membership_capability,
                )
                if retired_peer_id:
                    conn.execute("COMMIT")
                    return {
                        "ok": True,
                        "local_peer_id": retired_peer_id,
                        "idempotent": True,
                    }
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": str(exc)}

            if actor["owner"]:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "owner_must_end_session"}

            participant_id = str(actor["membership_id"]).removeprefix("member:")
            updated = conn.execute(
                """UPDATE participants SET revoked_at = ?
                   WHERE id = ? AND session_id = ? AND revoked_at IS NULL""",
                (now, participant_id, session_id),
            )
            if updated.rowcount != 1:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "membership_state_conflict"}

            # Membership removal invalidates every key and signaling artifact of
            # the former pair. The next guest starts from a fresh security epoch
            # and cannot consume SDP/ICE retained for the departed device.
            conn.execute(
                "UPDATE sessions SET security_epoch = security_epoch + 1 WHERE id = ?",
                (session_id,),
            )
            conn.execute("DELETE FROM key_confirmations WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM signals WHERE session_id = ?", (session_id,))
            conn.execute("COMMIT")
            return {
                "ok": True,
                "local_peer_id": actor["peer_id"],
                "idempotent": False,
            }

    def authenticate_session_membership(
        self,
        *,
        session_id: str,
        account_id: str,
        requested_peer_id: str = "",
        membership_capability: str = "",
        require_pair: bool = False,
    ) -> dict[str, Any]:
        """Public application port for authentication before peer-scoped limits."""
        self._runtime.ensure_schema()
        with self._runtime.connect() as conn:
            session = get_session_by_id(conn, session_id)
            if not session:
                return {"ok": False, "reason": "session_not_found"}
            if session.get("revoked_at") is not None or float(session.get("expires_at") or 0) <= self._runtime.clock():
                return {"ok": False, "reason": "session_inactive"}
            try:
                if require_pair:
                    validated_strict_memberships(conn, session, require_pair=True)
                member = resolve_authenticated_membership(
                    conn,
                    session,
                    account_id=account_id,
                    requested_peer_id=requested_peer_id,
                    membership_capability=membership_capability,
                )
            except ValueError as exc:
                return {"ok": False, "reason": str(exc)}
            return {
                "ok": True,
                "local_peer_id": member["peer_id"],
                "identity_binding_version": int(session.get("identity_binding_version") or 0),
            }

    def set_membership_runtime(
        self,
        *,
        session_id: str,
        account_id: str,
        requested_peer_id: str,
        membership_capability: str,
        state: str,
    ) -> dict[str, Any]:
        """Set one exact v2 membership runtime under the one-active-peer invariant."""
        self._runtime.ensure_schema()
        if state not in {"active", "parked"}:
            return {"ok": False, "reason": "runtime_state_invalid"}
        with self._runtime.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            session = get_session_by_id(conn, session_id)
            if not session:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "session_not_found"}
            if session.get("revoked_at") is not None or float(session.get("expires_at") or 0) <= self._runtime.clock():
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "session_inactive"}
            try:
                member = resolve_authenticated_membership(
                    conn,
                    session,
                    account_id=account_id,
                    requested_peer_id=requested_peer_id,
                    membership_capability=membership_capability,
                )
                runtime = set_membership_runtime_locked(conn, session, member, state, clock=self._runtime.clock)
            except ValueError as exc:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": str(exc)}
            conn.execute("COMMIT")
            return {
                "ok": True,
                "local_peer_id": str(member["peer_id"]),
                **runtime,
            }

    def revoke_session(
        self,
        *,
        session_id: str,
        actor_user_id: str,
        actor_peer_id: str = "",
        membership_capability: str = "",
    ) -> dict[str, Any]:
        self._runtime.ensure_schema()
        with self._runtime.connect() as conn:
            session = get_session_by_id(conn, session_id)
            if not session:
                return {"ok": False, "reason": "session_not_found"}
            try:
                actor = resolve_authenticated_membership(
                    conn,
                    session,
                    account_id=actor_user_id,
                    requested_peer_id=actor_peer_id,
                    membership_capability=membership_capability,
                    owner_required=True,
                )
            except ValueError as exc:
                return {"ok": False, "reason": str(exc)}
            conn.execute("BEGIN IMMEDIATE")
            capability_lookup_hash = str(session.get("_owner_capability_lookup_hash") or "")
            if capability_lookup_hash:
                conn.execute(
                    """INSERT OR IGNORE INTO retired_owner_capabilities (
                           owner_capability_lookup_hash, retired_at
                       ) VALUES (?, ?)""",
                    (capability_lookup_hash, self._runtime.clock()),
                )
            conn.execute(
                "UPDATE sessions SET revoked_at = ?, invite_code = '' WHERE id = ?",
                (self._runtime.clock(), session_id),
            )
            conn.execute("DELETE FROM key_confirmations WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM signals WHERE session_id = ?", (session_id,))
            conn.execute("COMMIT")
        return {"ok": True, "local_peer_id": actor["peer_id"]}

    def is_authorized_participant(
        self,
        session_id: str,
        user_id: str,
        *,
        requester_peer_id: str = "",
        membership_capability: str = "",
    ) -> bool:
        self._runtime.ensure_schema()
        with self._runtime.connect() as conn:
            session = get_session_by_id(conn, session_id)
            if (
                not session
                or session.get("revoked_at") is not None
                or float(session.get("expires_at") or 0) <= self._runtime.clock()
                or session.get("security_mode") != "strict_e2ee"
                or int(session.get("security_contract_version") or 0) != 1
                or int(session.get("identity_binding_version") or 0) not in {1, 2}
            ):
                return False
            try:
                resolve_authenticated_membership(
                    conn,
                    session,
                    account_id=user_id,
                    requested_peer_id=requester_peer_id,
                    membership_capability=membership_capability,
                )
            except ValueError:
                return False
            return True


__all__ = ["SessionMembershipOperations"]
