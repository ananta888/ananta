"""Row projections and identity digests of the public rendezvous store.

Pure functions over SQLite rows and plain dictionaries: session/participant
projection, membership views, catalog items and the pseudonymous identity
digests. They take every input explicitly (no clock, no configuration module)
so the use-case collaborators and ``service`` share one definition (SRP).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from typing import Any

from pair_security import public_media_capabilities_for_version
from peer_identity import is_canonical_account_id

PROTOCOL_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}\Z")


def subject_hash(issuer: str, sub: str) -> str:
    """Persist only a pseudonymous digest of the canonical OIDC identity."""
    material = (
        b"ananta.public-rendezvous.subject-hash.v1\0"
        + str(issuer or "").strip().rstrip("/").encode("utf-8")
        + b"\0"
        + str(sub or "").strip().encode("utf-8")
    )
    return hashlib.sha256(material).hexdigest()[:16]


def owner_create_request_digest(
    *,
    account_id: str,
    peer_id: str,
    subject_hash: str,
    device_id: str,
    device_fingerprint: str,
    public_key_spki_b64: str,
    oidc_issuer: str,
    title: str,
    permissions: dict[str, bool],
    requested_expires_at: float | None,
    public_media_e2ee_version: int,
) -> str:
    payload = {
        "account_id": account_id,
        "device_fingerprint": device_fingerprint,
        "device_id": device_id,
        "identity_binding_version": 2,
        "oidc_issuer": oidc_issuer.rstrip("/"),
        "peer_id": peer_id,
        "permissions": permissions,
        "public_key_spki_b64": public_key_spki_b64,
        "requested_expires_at": requested_expires_at,
        "subject_hash": subject_hash,
        "title": title,
    }
    # Preserve the byte-for-byte v2 digest for pre-media clients and already
    # persisted idempotency records. Media fields are bound only when the
    # owner explicitly opts into one complete closed capability set.
    if public_media_e2ee_version:
        payload["public_media_e2ee_version"] = public_media_e2ee_version
        payload["public_media_capabilities"] = public_media_capabilities_for_version(public_media_e2ee_version)
    material = b"ananta.public-rendezvous.owner-create-request.v2\0" + json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def is_canonical_peer_id(value: str) -> bool:
    """Backward-compatible v1 name for the account-scoped identifier."""
    return is_canonical_account_id(value)


def is_protocol_identifier(value: str) -> bool:
    """Match the closed identifier grammar consumed by signed Pair packages."""
    return PROTOCOL_IDENTIFIER.fullmatch(str(value or "")) is not None


def new_invite_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(10))


def parse_permissions(raw: str | None) -> dict[str, bool]:
    if not raw:
        return {}
    data = json.loads(raw)
    if isinstance(data, dict):
        return {str(k): bool(v) for k, v in data.items()}
    return {}


def row_to_session(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "owner_user_id": row["owner_user_id"],
        "owner_user_sub_hash": row["owner_user_sub_hash"],
        "owner_device_fingerprint": row["owner_device_fingerprint"],
        "oidc_issuer": row["oidc_issuer"],
        "title": row["title"],
        "invite_code": row["invite_code"] or "",
        "allowed_permissions": parse_permissions(row["allowed_permissions"]),
        "expires_at": row["expires_at"],
        "created_at": row["created_at"],
        "revoked_at": row["revoked_at"],
        "owner_device_id": row["owner_device_id"],
        "owner_public_key_spki_b64": row["owner_public_key_spki_b64"],
        "owner_account_id": row["owner_account_id"],
        "owner_peer_id": row["owner_peer_id"],
        "_owner_membership_capability_hash": row["owner_membership_capability_hash"],
        "_owner_capability_lookup_hash": row["owner_capability_lookup_hash"],
        "_owner_create_request_hash": row["owner_create_request_hash"],
        "security_epoch": row["security_epoch"],
        "security_contract_version": row["security_contract_version"],
        "identity_binding_version": row["identity_binding_version"],
        "public_media_e2ee_version": row["public_media_e2ee_version"],
        "public_media_capabilities": public_media_capabilities_for_version(int(row["public_media_e2ee_version"] or 0)),
        "security_mode": row["security_mode"],
        "mode": "p2p",
        "transport": "webrtc",
        "permissions_version": 1,
        "_owner_runtime_state": row["owner_runtime_state"],
    }


def list_participants(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    include_revoked: bool = False,
) -> list[dict[str, Any]]:
    where_clause = "" if include_revoked else "AND revoked_at IS NULL"
    rows = conn.execute(
        f"""
        SELECT id, session_id, user_id, user_sub_hash, device_id,
               device_fingerprint, public_key_spki_b64, permissions,
               joined_at, last_seen, revoked_at, account_id, peer_id,
               membership_capability_hash, public_media_e2ee_version
               , runtime_state
        FROM participants
        WHERE session_id = ? {where_clause}
        ORDER BY joined_at ASC
        """,
        (session_id,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "user_id": row["user_id"],
                "user_sub_hash": row["user_sub_hash"],
                "device_id": row["device_id"],
                "device_fingerprint": row["device_fingerprint"],
                "permissions": parse_permissions(row["permissions"]),
                "joined_at": row["joined_at"],
                "last_seen": row["last_seen"],
                "revoked_at": row["revoked_at"],
                "public_key_spki_b64": row["public_key_spki_b64"],
                "account_id": row["account_id"],
                "peer_id": row["peer_id"],
                "_membership_capability_hash": row["membership_capability_hash"],
                "public_media_e2ee_version": row["public_media_e2ee_version"],
                "public_media_capabilities": public_media_capabilities_for_version(
                    int(row["public_media_e2ee_version"] or 0)
                ),
                "_runtime_state": row["runtime_state"],
            }
        )
    return out


def session_snapshot(
    conn: sqlite3.Connection,
    session: dict[str, Any],
    *,
    include_participants: bool = False,
) -> dict[str, Any]:
    sid = str(session.get("id") or "")
    participants = list_participants(conn, sid, include_revoked=False)
    permissions = dict(session.get("allowed_permissions") or {})
    out = {key: value for key, value in session.items() if not key.startswith("_")}
    out["permissions"] = permissions
    out["participant_count"] = len(participants)
    if include_participants:
        out["participants"] = [
            {key: value for key, value in participant.items() if not key.startswith("_")}
            for participant in participants
        ]
    return out


def get_session_by_id(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        return None
    return row_to_session(row)


def session_memberships(conn: sqlite3.Connection, session: dict[str, Any]) -> list[dict[str, Any]]:
    identity_binding_version = int(session.get("identity_binding_version") or 0)
    owner_account_id = str(session.get("owner_account_id") or session.get("owner_user_id") or "")
    owner_peer_id = (
        str(session.get("owner_peer_id") or "")
        if identity_binding_version == 2
        else str(session.get("owner_user_id") or "")
    )
    owner = {
        "membership_id": f"member:{session['id']}:owner",
        "membership_version": 1,
        "account_id": owner_account_id,
        "peer_id": owner_peer_id,
        "device_id": session["owner_device_id"],
        "fingerprint": session["owner_device_fingerprint"],
        "public_key_spki_b64": session["owner_public_key_spki_b64"],
        "_membership_capability_hash": session.get("_owner_membership_capability_hash") or "",
        "public_media_e2ee_version": int(session.get("public_media_e2ee_version") or 0),
        "runtime_state": str(session.get("_owner_runtime_state") or "active"),
        "owner": True,
    }
    members = [owner]
    for participant in list_participants(conn, session["id"]):
        members.append(
            {
                "membership_id": f"member:{participant['id']}",
                "membership_version": 1,
                "account_id": participant.get("account_id") or participant["user_id"],
                "peer_id": (
                    participant.get("peer_id") or "" if identity_binding_version == 2 else participant["user_id"]
                ),
                "device_id": participant["device_id"],
                "fingerprint": participant["device_fingerprint"],
                "public_key_spki_b64": participant["public_key_spki_b64"],
                "_membership_capability_hash": participant.get("_membership_capability_hash") or "",
                "public_media_e2ee_version": int(participant.get("public_media_e2ee_version") or 0),
                "runtime_state": str(participant.get("_runtime_state") or "active"),
                "owner": False,
            }
        )
    return members


def catalog_peer_label(session_id: str, peer_id: str, *, signing_secret: str) -> str:
    """Return a display-only peer label without exposing a stable peer id."""
    digest = hmac.new(
        signing_secret.encode("utf-8"),
        b"ananta.public-rendezvous.catalog-peer.v1\0"
        + session_id.encode("utf-8")
        + b"\0"
        + peer_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"Peer-{digest[:6]}"


def session_catalog_item(
    session: dict[str, Any],
    memberships: list[dict[str, Any]],
    selected: dict[str, Any],
    *,
    signing_secret: str,
) -> dict[str, Any]:
    """Project the minimum authenticated metadata needed to switch sessions."""
    local_peer_id = str(selected["peer_id"])
    permissions = dict(session.get("allowed_permissions") or {})
    item: dict[str, Any] = {
        "id": str(session["id"]),
        "title": str(session.get("title") or ""),
        "permissions": permissions,
        "created_at": session.get("created_at"),
        "expires_at": session.get("expires_at"),
        "revoked_at": session.get("revoked_at"),
        "security_epoch": int(session.get("security_epoch") or 0),
        "security_contract_version": int(session.get("security_contract_version") or 0),
        "security_mode": str(session.get("security_mode") or ""),
        "identity_binding_version": int(session.get("identity_binding_version") or 0),
        "mode": "p2p",
        "transport": "webrtc",
        "permissions_version": 1,
        "participant_count": max(0, len(memberships) - 1),
        "local_peer_id": local_peer_id,
        # Preserve the additive discovery shape without revealing other device
        # memberships owned by the same account.
        "local_peer_ids": [local_peer_id],
        "local_runtime_state": str(selected.get("runtime_state") or "active"),
    }
    item["local_role"] = "owner" if selected["owner"] else "participant"
    if selected["owner"]:
        item["invite_code"] = str(session.get("invite_code") or "")
    remote = next(
        (member for member in memberships if str(member["peer_id"]) != local_peer_id),
        None,
    )
    if remote is not None:
        item["peer_label"] = catalog_peer_label(
            str(session["id"]),
            str(remote["peer_id"]),
            signing_secret=signing_secret,
        )
    return item


__all__ = [
    "catalog_peer_label",
    "get_session_by_id",
    "is_canonical_peer_id",
    "is_protocol_identifier",
    "list_participants",
    "new_invite_code",
    "owner_create_request_digest",
    "parse_permissions",
    "row_to_session",
    "session_catalog_item",
    "session_memberships",
    "session_snapshot",
    "subject_hash",
]
