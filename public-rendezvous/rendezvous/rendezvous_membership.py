"""Strict pair membership validation, authentication and runtime state.

Functions over one open SQLite connection. Time-dependent checks receive the
clock explicitly, so the owner (``service``) keeps the single clock seam and
every use case validates memberships through the same rules (SRP/DIP).
"""

from __future__ import annotations

import secrets
import sqlite3
from typing import Any, Callable

from pair_security import spki_fingerprint
from peer_identity import (
    canonical_device_peer_id,
    is_canonical_account_id,
    is_device_peer_id,
    is_membership_capability,
    membership_capability_digest,
)
from rendezvous_records import get_session_by_id, is_canonical_peer_id, session_memberships


def invalidate_pair_runtime_locked(conn: sqlite3.Connection, session_id: str) -> int:
    """Advance the crypto fence and remove every prior transport artifact."""
    conn.execute(
        "UPDATE sessions SET security_epoch = security_epoch + 1 WHERE id = ?",
        (session_id,),
    )
    conn.execute("DELETE FROM key_confirmations WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM signals WHERE session_id = ?", (session_id,))
    row = conn.execute(
        "SELECT security_epoch FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if not row:
        raise RuntimeError("runtime session disappeared")
    return int(row["security_epoch"])


def write_membership_runtime_locked(
    conn: sqlite3.Connection,
    session_id: str,
    member: dict[str, Any],
    state: str,
) -> None:
    if member["owner"]:
        updated = conn.execute(
            "UPDATE sessions SET owner_runtime_state = ? WHERE id = ?",
            (state, session_id),
        )
    else:
        participant_id = str(member["membership_id"]).removeprefix("member:")
        updated = conn.execute(
            """UPDATE participants SET runtime_state = ?
               WHERE id = ? AND session_id = ? AND revoked_at IS NULL""",
            (state, participant_id, session_id),
        )
    if updated.rowcount != 1:
        raise RuntimeError("membership runtime update conflict")


def active_memberships_for_peer_locked(
    conn: sqlite3.Connection,
    *,
    account_id: str,
    peer_id: str,
    excluded_session_id: str,
    clock: Callable[[], float],
) -> list[tuple[str, bool]]:
    now = clock()
    rows = conn.execute(
        """SELECT s.id AS session_id, 1 AS owner
           FROM sessions s
           WHERE s.identity_binding_version = 2
             AND s.owner_account_id = ? AND s.owner_peer_id = ?
             AND s.owner_runtime_state = 'active'
             AND s.id != ? AND s.revoked_at IS NULL AND s.expires_at > ?
           UNION ALL
           SELECT s.id AS session_id, 0 AS owner
           FROM participants p
           JOIN sessions s ON s.id = p.session_id
           WHERE s.identity_binding_version = 2
             AND p.account_id = ? AND p.peer_id = ?
             AND p.runtime_state = 'active'
             AND p.revoked_at IS NULL
             AND s.id != ? AND s.revoked_at IS NULL AND s.expires_at > ?
           ORDER BY session_id""",
        (
            account_id,
            peer_id,
            excluded_session_id,
            now,
            account_id,
            peer_id,
            excluded_session_id,
            now,
        ),
    ).fetchall()
    return [(str(row["session_id"]), bool(row["owner"])) for row in rows]


def set_membership_runtime_locked(
    conn: sqlite3.Connection,
    session: dict[str, Any],
    member: dict[str, Any],
    state: str,
    *,
    clock: Callable[[], float],
) -> dict[str, Any]:
    """Apply one exact runtime state and enforce one-active-peer atomically."""
    if state not in {"active", "parked"}:
        raise ValueError("runtime_state_invalid")
    if int(session.get("identity_binding_version") or 0) != 2:
        raise ValueError("runtime_state_requires_identity_v2")

    session_id = str(session["id"])
    current_state = str(member.get("runtime_state") or "active")
    parked_session_ids: list[str] = []
    if state == "active":
        other_memberships = active_memberships_for_peer_locked(
            conn,
            account_id=str(member["account_id"]),
            peer_id=str(member["peer_id"]),
            excluded_session_id=session_id,
            clock=clock,
        )
        for other_session_id, owner in other_memberships:
            if owner:
                conn.execute(
                    "UPDATE sessions SET owner_runtime_state = 'parked' WHERE id = ?",
                    (other_session_id,),
                )
            else:
                conn.execute(
                    """UPDATE participants SET runtime_state = 'parked'
                       WHERE session_id = ? AND account_id = ? AND peer_id = ?
                         AND revoked_at IS NULL""",
                    (other_session_id, member["account_id"], member["peer_id"]),
                )
            invalidate_pair_runtime_locked(conn, other_session_id)
            parked_session_ids.append(other_session_id)

    target_changed = current_state != state
    changed = target_changed or bool(parked_session_ids)
    if target_changed:
        write_membership_runtime_locked(conn, session_id, member, state)
    # Selecting an already-active target while another session was active is
    # still a new transport generation. Fence stale target SDP together with
    # the peers that were atomically parked.
    if changed:
        security_epoch = invalidate_pair_runtime_locked(conn, session_id)
    else:
        security_epoch = int(session.get("security_epoch") or 0)
    if state == "parked" and target_changed:
        parked_session_ids.append(session_id)
    return {
        "state": state,
        "security_epoch": security_epoch,
        "changed": changed,
        "parked_session_ids": sorted(set(parked_session_ids)),
    }


def activate_joined_v2_membership_locked(
    conn: sqlite3.Connection,
    session: dict[str, Any],
    peer_id: str,
    *,
    clock: Callable[[], float],
) -> dict[str, Any]:
    """Activate an exact joined device and return its refreshed session."""
    if int(session.get("identity_binding_version") or 0) != 2:
        return session
    member = next(
        (
            candidate
            for candidate in session_memberships(conn, session)
            if str(candidate["peer_id"]) == peer_id
        ),
        None,
    )
    if member is None:
        raise RuntimeError("joined membership not found")
    set_membership_runtime_locked(conn, session, member, "active", clock=clock)
    refreshed = get_session_by_id(conn, str(session["id"]))
    if refreshed is None:
        raise RuntimeError("joined session not found")
    return refreshed


def retired_guest_peer_for_capability(
    conn: sqlite3.Connection,
    session: dict[str, Any],
    *,
    account_id: str,
    requested_peer_id: str,
    membership_capability: str,
) -> str:
    """Resolve only an exact, already-revoked guest for DELETE idempotency."""
    identity_binding_version = int(session.get("identity_binding_version") or 0)
    if (
        session.get("security_mode") != "strict_e2ee"
        or int(session.get("security_contract_version") or 0) != 1
    ):
        return ""
    requested_peer_id = str(requested_peer_id or "").strip()
    if identity_binding_version == 2:
        if not requested_peer_id or not is_membership_capability(membership_capability):
            return ""
        rows = conn.execute(
            """SELECT peer_id, membership_capability_hash
               FROM participants
               WHERE session_id = ? AND account_id = ? AND peer_id = ?
                 AND revoked_at IS NOT NULL""",
            (session["id"], account_id, requested_peer_id),
        ).fetchall()
        expected_hash = membership_capability_digest(
            str(session["id"]),
            account_id,
            requested_peer_id,
            membership_capability,
        )
        for row in rows:
            if secrets.compare_digest(
                str(row["membership_capability_hash"] or ""),
                expected_hash,
            ):
                return str(row["peer_id"])
        return ""

    # Identity-binding v1 predates membership capabilities. Its canonical
    # account id is also the peer id, so the authenticated account is the
    # complete idempotency boundary.
    if identity_binding_version != 1 or (requested_peer_id and requested_peer_id != account_id):
        return ""
    row = conn.execute(
        """SELECT user_id FROM participants
           WHERE session_id = ? AND user_id = ? AND revoked_at IS NOT NULL
           ORDER BY revoked_at DESC LIMIT 1""",
        (session["id"], account_id),
    ).fetchone()
    return str(row["user_id"]) if row else ""


def validated_strict_memberships(
    conn: sqlite3.Connection,
    session: dict[str, Any],
    *,
    require_pair: bool,
) -> list[dict[str, Any]]:
    """Return current pair membership after validating its security binding."""
    identity_binding_version = int(session.get("identity_binding_version") or 0)
    if (
        session.get("security_mode") != "strict_e2ee"
        or int(session.get("security_contract_version") or 0) != 1
        or identity_binding_version not in {1, 2}
    ):
        raise ValueError("strict_e2ee_required")
    members = session_memberships(conn, session)
    expected_count = 2 if require_pair else None
    if expected_count is not None and len(members) != expected_count:
        raise ValueError("strict_pair_membership_incomplete")
    if len(members) > 2:
        raise ValueError("strict_pair_cardinality_exceeded")
    for member in members:
        account_id = str(member.get("account_id") or "")
        peer_id = str(member.get("peer_id") or "")
        if not is_canonical_account_id(account_id):
            raise ValueError("account_identity_binding_invalid")
        if identity_binding_version == 1:
            if not is_canonical_peer_id(peer_id) or peer_id != account_id:
                raise ValueError("peer_identity_binding_invalid")
        else:
            if not is_device_peer_id(peer_id):
                raise ValueError("peer_identity_binding_invalid")
            expected_peer_id = canonical_device_peer_id(
                account_id,
                str(member.get("fingerprint") or ""),
            )
            if not secrets.compare_digest(peer_id, expected_peer_id):
                raise ValueError("peer_identity_binding_invalid")
            capability_hash = str(member.get("_membership_capability_hash") or "")
            if len(capability_hash) != 64 or any(char not in "0123456789abcdef" for char in capability_hash):
                raise ValueError("membership_capability_binding_invalid")
        public_key = str(member.get("public_key_spki_b64") or "")
        fingerprint = str(member.get("fingerprint") or "").lower()
        try:
            actual_fingerprint = spki_fingerprint(public_key)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if not secrets.compare_digest(actual_fingerprint, fingerprint):
            raise ValueError("device_key_substitution")
    if len(members) == 2:
        owner, guest = members
        if secrets.compare_digest(str(owner["peer_id"]), str(guest["peer_id"])):
            raise ValueError("peer_identity_must_be_distinct")
        if secrets.compare_digest(str(owner["fingerprint"]), str(guest["fingerprint"])):
            raise ValueError("device_key_must_be_distinct")
    return members


def resolve_authenticated_membership(
    conn: sqlite3.Connection,
    session: dict[str, Any],
    *,
    account_id: str,
    requested_peer_id: str = "",
    membership_capability: str = "",
    owner_required: bool = False,
) -> dict[str, Any]:
    """Resolve one session membership without trusting a client peer selector."""
    members = validated_strict_memberships(conn, session, require_pair=False)
    identity_binding_version = int(session.get("identity_binding_version") or 0)
    account_members = [member for member in members if member["account_id"] == account_id]
    if not account_members:
        raise ValueError("forbidden")
    requested_peer_id = str(requested_peer_id or "").strip()
    if identity_binding_version == 1:
        member = next(
            (candidate for candidate in account_members if candidate["peer_id"] == account_id),
            None,
        )
        if member is None or (requested_peer_id and requested_peer_id != member["peer_id"]):
            raise ValueError("forbidden")
    else:
        if not requested_peer_id:
            raise ValueError("local_peer_id_required")
        member = next(
            (candidate for candidate in account_members if candidate["peer_id"] == requested_peer_id),
            None,
        )
        if member is None:
            raise ValueError("forbidden")
        if not membership_capability:
            raise ValueError("membership_capability_required")
        if not is_membership_capability(membership_capability):
            raise ValueError("membership_capability_invalid")
        expected_hash = membership_capability_digest(
            str(session["id"]),
            account_id,
            requested_peer_id,
            membership_capability,
        )
        if not secrets.compare_digest(
            str(member.get("_membership_capability_hash") or ""),
            expected_hash,
        ):
            raise ValueError("membership_capability_invalid")
    if owner_required and not member["owner"]:
        raise ValueError("forbidden")
    return member


def pair_runtime_active(members: list[dict[str, Any]]) -> bool:
    return len(members) == 2 and all(
        str(member.get("runtime_state") or "active") == "active"
        for member in members
    )


def require_pair_runtime_active(
    session: dict[str, Any],
    members: list[dict[str, Any]],
) -> None:
    if int(session.get("identity_binding_version") or 0) == 2 and not pair_runtime_active(members):
        raise ValueError("pair_runtime_not_ready")


def mutual_confirmations_ready_locked(
    conn: sqlite3.Connection,
    session: dict[str, Any],
    members: list[dict[str, Any]],
    *,
    clock: Callable[[], float],
) -> bool:
    if len(members) != 2:
        return False
    peer_ids = {str(member["peer_id"]) for member in members}
    rows = conn.execute(
        """SELECT sender_peer_id, recipient_peer_id
           FROM key_confirmations
           WHERE session_id = ? AND epoch = ? AND expires_at > ?""",
        (session["id"], int(session["security_epoch"]), clock()),
    ).fetchall()
    directions = {
        (str(row["sender_peer_id"]), str(row["recipient_peer_id"]))
        for row in rows
        if str(row["sender_peer_id"]) in peer_ids and str(row["recipient_peer_id"]) in peer_ids
    }
    return len(directions) == 2 and all(
        sender != recipient
        and (recipient, sender) in directions
        for sender, recipient in directions
    )


def require_transport_ready_locked(
    conn: sqlite3.Connection,
    session: dict[str, Any],
    members: list[dict[str, Any]],
    *,
    clock: Callable[[], float],
) -> None:
    if int(session.get("identity_binding_version") or 0) != 2:
        return
    require_pair_runtime_active(session, members)
    if not mutual_confirmations_ready_locked(conn, session, members, clock=clock):
        raise ValueError("pair_runtime_not_ready")


__all__ = [
    "activate_joined_v2_membership_locked",
    "active_memberships_for_peer_locked",
    "invalidate_pair_runtime_locked",
    "mutual_confirmations_ready_locked",
    "pair_runtime_active",
    "require_pair_runtime_active",
    "require_transport_ready_locked",
    "resolve_authenticated_membership",
    "retired_guest_peer_for_capability",
    "set_membership_runtime_locked",
    "validated_strict_memberships",
    "write_membership_runtime_locked",
]
