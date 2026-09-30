"""Signed Pair key packages and bilateral key confirmations.

Owns the key-package projection and the short-lived confirmation leases of
one strict Pair session. The signing authority, clock and store come from the
injected :class:`RendezvousRuntime` (SRP/DIP).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import sqlite3
from typing import Any

from pair_security import (
    PUBLIC_MEDIA_E2EE_VERSION,
    PUBLIC_MEDIA_E2EE_VERSION_V2,
    TENANT_ID,
    PairSecurityAuthority,
)
from rendezvous_membership import (
    pair_runtime_active,
    require_pair_runtime_active,
    resolve_authenticated_membership,
    validated_strict_memberships,
)
from rendezvous_records import get_session_by_id
from rendezvous_runtime import RendezvousRuntime


def strict_pair_material(
    conn: sqlite3.Connection,
    session: dict[str, Any],
    authority: PairSecurityAuthority,
) -> tuple[list[dict[str, Any]], PairSecurityAuthority, dict[str, Any]]:
    members = validated_strict_memberships(conn, session, require_pair=True)
    owner = next(member for member in members if member["owner"])
    guest = next(member for member in members if not member["owner"])
    return members, authority, authority.contract(session, owner, guest)


def public_media_contract(
    *,
    session: dict[str, Any],
    members: list[dict[str, Any]],
    authority: PairSecurityAuthority,
    base_contract: dict[str, Any],
    public_media_e2ee_version: int,
) -> dict[str, Any] | None:
    """Negotiate one media contract only for an exact bilateral advert."""
    if int(session.get("identity_binding_version") or 0) != 2 or len(members) != 2:
        return None
    if any(int(member.get("public_media_e2ee_version") or 0) != public_media_e2ee_version for member in members):
        return None
    owner = next(member for member in members if member["owner"])
    guest = next(member for member in members if not member["owner"])
    return authority.public_media_contract(
        session=session,
        owner=owner,
        guest=guest,
        base_security_contract_digest=str(base_contract["digest"]),
        public_media_e2ee_version=public_media_e2ee_version,
    )


def expected_key_package(
    *,
    members: list[dict[str, Any]],
    authority: PairSecurityAuthority,
    session: dict[str, Any],
    contract: dict[str, Any],
    sender_peer_id: str,
    recipient_peer_id: str,
) -> dict[str, Any]:
    """Return the package a sender must have received for this direction."""
    sender = next((member for member in members if member["peer_id"] == sender_peer_id), None)
    recipient = next((member for member in members if member["peer_id"] == recipient_peer_id), None)
    if sender is None or recipient is None or sender is recipient:
        raise ValueError("forbidden")
    return authority.key_package(
        session=session,
        membership=recipient,
        recipient_peer_id=sender["peer_id"],
        contract_digest=str(contract["digest"]),
    )


class PairKeyExchange:
    """Serve key packages and store bilateral key confirmations."""

    def __init__(self, *, runtime: RendezvousRuntime, confirmation_ttl_seconds: int) -> None:
        self._runtime = runtime
        self._confirmation_ttl_seconds = confirmation_ttl_seconds

    def get_key_packages(
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
                members = validated_strict_memberships(conn, session, require_pair=False)
            except ValueError as exc:
                return {"ok": False, "reason": str(exc)}
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
            if len(members) == 1:
                authority = self._runtime.security_authority
                return {
                    "ok": True,
                    "epoch": session["security_epoch"],
                    "tenant_id": TENANT_ID,
                    "security_contract_digest": None,
                    "security_contract": None,
                    "public_media_security_contract_v1": None,
                    "public_media_security_contract_v2": None,
                    "hub_key_id": authority.key_id,
                    "hub_public_key_b64": authority.public_key_b64,
                    "packages": [],
                    "local_membership_id": requester["membership_id"],
                    "local_peer_id": requester["peer_id"],
                    "local_package_id": None,
                    "transport_ready": False,
                    "local_runtime_state": str(requester.get("runtime_state") or "active"),
                    "peer_runtime_state": "missing",
                }
            if int(session.get("identity_binding_version") or 0) == 2 and not pair_runtime_active(members):
                authority = self._runtime.security_authority
                remote = next(member for member in members if member["peer_id"] != requester["peer_id"])
                return {
                    "ok": True,
                    "epoch": session["security_epoch"],
                    "tenant_id": TENANT_ID,
                    "security_contract_digest": None,
                    "security_contract": None,
                    "public_media_security_contract_v1": None,
                    "public_media_security_contract_v2": None,
                    "hub_key_id": authority.key_id,
                    "hub_public_key_b64": authority.public_key_b64,
                    "packages": [],
                    "local_membership_id": requester["membership_id"],
                    "local_peer_id": requester["peer_id"],
                    "local_package_id": None,
                    "transport_ready": False,
                    "local_runtime_state": str(requester.get("runtime_state") or "active"),
                    "peer_runtime_state": str(remote.get("runtime_state") or "active"),
                }
            try:
                members, authority, contract = strict_pair_material(conn, session, self._runtime.security_authority)
            except ValueError as exc:
                return {"ok": False, "reason": str(exc)}
            requester = next(member for member in members if member["peer_id"] == requester["peer_id"])
            remote = next(member for member in members if member["peer_id"] != requester["peer_id"])
            package = authority.key_package(
                session=session,
                membership=remote,
                recipient_peer_id=requester["peer_id"],
                contract_digest=contract["digest"],
            )
            local_package = expected_key_package(
                members=members,
                authority=authority,
                session=session,
                contract=contract,
                sender_peer_id=remote["peer_id"],
                recipient_peer_id=requester["peer_id"],
            )
            public_media_contract_v1 = public_media_contract(
                session=session,
                members=members,
                authority=authority,
                base_contract=contract,
                public_media_e2ee_version=PUBLIC_MEDIA_E2EE_VERSION,
            )
            public_media_contract_v2 = public_media_contract(
                session=session,
                members=members,
                authority=authority,
                base_contract=contract,
                public_media_e2ee_version=PUBLIC_MEDIA_E2EE_VERSION_V2,
            )
            return {
                "ok": True,
                "epoch": session["security_epoch"],
                "tenant_id": TENANT_ID,
                "security_contract_digest": contract["digest"],
                "security_contract": contract,
                "public_media_security_contract_v1": public_media_contract_v1,
                "public_media_security_contract_v2": public_media_contract_v2,
                "hub_key_id": authority.key_id,
                "hub_public_key_b64": authority.public_key_b64,
                "packages": [package],
                "local_membership_id": requester["membership_id"],
                "local_peer_id": requester["peer_id"],
                "local_package_id": local_package["package_id"],
                "transport_ready": True,
                "local_runtime_state": str(requester.get("runtime_state") or "active"),
                "peer_runtime_state": str(remote.get("runtime_state") or "active"),
            }

    def put_key_confirmation(
        self,
        *,
        session_id: str,
        sender_peer_id: str,
        recipient_peer_id: str,
        package_id: str,
        epoch: int,
        confirmation_tag: str,
        sender_account_id: str = "",
        membership_capability: str = "",
    ) -> dict[str, Any]:
        self._runtime.ensure_schema()
        package_id = str(package_id or "").strip().lower()
        confirmation_tag = str(confirmation_tag or "").strip()
        try:
            raw_tag = base64.b64decode(confirmation_tag, validate=True)
        except (ValueError, TypeError):
            return {"ok": False, "reason": "confirmation_tag_invalid"}
        if len(raw_tag) != 32 or len(package_id) != 64 or any(char not in "0123456789abcdef" for char in package_id):
            return {"ok": False, "reason": "confirmation_invalid"}

        with self._runtime.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            session = get_session_by_id(conn, session_id)
            if not session:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "session_not_found"}
            now = self._runtime.clock()
            if session.get("revoked_at") is not None or float(session.get("expires_at") or 0) <= now:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "session_inactive"}
            try:
                members, authority, contract = strict_pair_material(conn, session, self._runtime.security_authority)
                sender = resolve_authenticated_membership(
                    conn,
                    session,
                    account_id=sender_account_id or sender_peer_id,
                    requested_peer_id=sender_peer_id,
                    membership_capability=membership_capability,
                )
                require_pair_runtime_active(session, members)
                if sender["peer_id"] != sender_peer_id:
                    raise ValueError("forbidden")
                expected_package = expected_key_package(
                    members=members,
                    authority=authority,
                    session=session,
                    contract=contract,
                    sender_peer_id=sender_peer_id,
                    recipient_peer_id=recipient_peer_id,
                )
            except ValueError as exc:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": str(exc)}
            if epoch != int(session["security_epoch"]):
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "epoch_mismatch"}
            if not secrets.compare_digest(str(expected_package["package_id"]), package_id):
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "key_package_binding_mismatch"}

            existing = conn.execute(
                """SELECT package_id, confirmation_tag, created_at, expires_at
                   FROM key_confirmations
                   WHERE session_id=? AND epoch=? AND sender_peer_id=? AND recipient_peer_id=?""",
                (session_id, epoch, sender_peer_id, recipient_peer_id),
            ).fetchone()
            if existing and float(existing["expires_at"] or 0) > now:
                if not (
                    secrets.compare_digest(str(existing["package_id"]), package_id)
                    and secrets.compare_digest(str(existing["confirmation_tag"]), confirmation_tag)
                ):
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "key_confirmation_conflict"}
                # A confirmation is a short-lived liveness lease over an immutable
                # direction/package/tag binding.  Both peers refresh that lease
                # before its five-minute deadline.  Returning the original expiry
                # here made every refresh a no-op, so the first peer crossing the
                # deadline observed the other direction as absent and tore down an
                # otherwise healthy E2EE transport.  Renew only after the caller's
                # membership and exact confirmation binding were re-authenticated
                # above; changed material remains a fail-closed conflict.
                renewed_expires_at = min(
                    float(session["expires_at"]),
                    now + self._confirmation_ttl_seconds,
                )
                conn.execute(
                    """UPDATE key_confirmations
                       SET created_at = ?, expires_at = ?
                       WHERE session_id = ? AND epoch = ?
                         AND sender_peer_id = ? AND recipient_peer_id = ?""",
                    (
                        now,
                        renewed_expires_at,
                        session_id,
                        epoch,
                        sender_peer_id,
                        recipient_peer_id,
                    ),
                )
                conn.execute("COMMIT")
                self._runtime.logger.info(
                    "pair_key_confirmation_renewed session=%s epoch=%d direction=%s expires_at=%d",
                    session_id,
                    epoch,
                    hashlib.sha256(f"{sender_peer_id}>{recipient_peer_id}".encode()).hexdigest()[:12],
                    int(renewed_expires_at),
                )
                return {
                    "ok": True,
                    "idempotent": True,
                    "created_at_ms": int(now * 1000),
                    "expires_at_ms": int(renewed_expires_at * 1000),
                }
            if existing:
                conn.execute(
                    """DELETE FROM key_confirmations
                       WHERE session_id=? AND epoch=? AND sender_peer_id=? AND recipient_peer_id=?""",
                    (session_id, epoch, sender_peer_id, recipient_peer_id),
                )
            expires_at = min(float(session["expires_at"]), now + self._confirmation_ttl_seconds)
            conn.execute(
                """
                INSERT INTO key_confirmations (
                    session_id, epoch, sender_peer_id, recipient_peer_id, package_id,
                    confirmation_tag, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    epoch,
                    sender_peer_id,
                    recipient_peer_id,
                    package_id,
                    confirmation_tag,
                    now,
                    expires_at,
                ),
            )
            conn.execute("COMMIT")
            self._runtime.logger.info(
                "pair_key_confirmation_stored session=%s epoch=%d direction=%s",
                session_id,
                epoch,
                hashlib.sha256(f"{sender_peer_id}>{recipient_peer_id}".encode()).hexdigest()[:12],
            )
            return {
                "ok": True,
                "created_at_ms": int(now * 1000),
                "expires_at_ms": int(expires_at * 1000),
            }

    def get_key_confirmation(
        self,
        *,
        session_id: str,
        requester_user_id: str,
        sender_peer_id: str,
        requester_peer_id: str = "",
        membership_capability: str = "",
    ) -> dict[str, Any]:
        self._runtime.ensure_schema()
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
                members, authority, contract = strict_pair_material(conn, session, self._runtime.security_authority)
                requester = resolve_authenticated_membership(
                    conn,
                    session,
                    account_id=requester_user_id,
                    requested_peer_id=requester_peer_id,
                    membership_capability=membership_capability,
                )
                require_pair_runtime_active(session, members)
                expected_package = expected_key_package(
                    members=members,
                    authority=authority,
                    session=session,
                    contract=contract,
                    sender_peer_id=sender_peer_id,
                    recipient_peer_id=requester["peer_id"],
                )
            except ValueError as exc:
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": str(exc)}
            row = conn.execute(
                """SELECT confirmation_tag, package_id, epoch, created_at, expires_at
                   FROM key_confirmations
                   WHERE session_id=? AND epoch=? AND sender_peer_id=? AND recipient_peer_id=?""",
                (session_id, session["security_epoch"], sender_peer_id, requester["peer_id"]),
            ).fetchone()
            now = self._runtime.clock()
            if row and float(row["expires_at"] or 0) <= now:
                conn.execute(
                    """DELETE FROM key_confirmations
                       WHERE session_id=? AND epoch=? AND sender_peer_id=? AND recipient_peer_id=?""",
                    (session_id, session["security_epoch"], sender_peer_id, requester["peer_id"]),
                )
                row = None
            if row and not secrets.compare_digest(str(row["package_id"]), str(expected_package["package_id"])):
                conn.execute("ROLLBACK")
                return {"ok": False, "reason": "key_confirmation_binding_invalid"}
            conn.execute("COMMIT")
            confirmation = (
                None
                if row is None
                else {
                    "confirmation_tag": row["confirmation_tag"],
                    "package_id": row["package_id"],
                    "epoch": row["epoch"],
                    "created_at_ms": int(float(row["created_at"]) * 1000),
                    "expires_at_ms": int(float(row["expires_at"]) * 1000),
                }
            )
            return {"ok": True, "confirmation": confirmation}


__all__ = ["PairKeyExchange", "expected_key_package", "public_media_contract", "strict_pair_material"]
