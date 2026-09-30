"""Account-scoped session listings for V1 snapshots and proof-validated V2 catalogs."""

from __future__ import annotations

import sqlite3
from typing import Any

from rendezvous_membership import resolve_authenticated_membership, validated_strict_memberships
from rendezvous_records import get_session_by_id, row_to_session, session_catalog_item, session_snapshot
from rendezvous_runtime import RendezvousRuntime


class SessionCatalog:
    """List the sessions an authenticated account may see."""

    def __init__(self, *, runtime: RendezvousRuntime) -> None:
        self._runtime = runtime

    def _account_v1_sessions_locked(
        self,
        conn: sqlite3.Connection,
        requester_user_id: str,
    ) -> list[tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]]:
        rows = conn.execute(
            """SELECT DISTINCT s.*
               FROM sessions s
               LEFT JOIN participants p
                 ON p.session_id = s.id AND p.revoked_at IS NULL
               WHERE s.revoked_at IS NULL
                 AND s.identity_binding_version = 1
                 AND (s.owner_user_id = ? OR p.user_id = ?)
               ORDER BY s.created_at DESC""",
            (requester_user_id, requester_user_id),
        ).fetchall()
        resolved: list[tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]] = []
        for row in rows:
            session = row_to_session(row)
            try:
                memberships = validated_strict_memberships(conn, session, require_pair=False)
            except ValueError:
                continue
            account_members = [member for member in memberships if member["account_id"] == requester_user_id]
            if len(account_members) == 1:
                resolved.append((session, memberships, account_members[0]))
        return resolved

    def list_sessions_for_user(self, *, requester_user_id: str) -> list[dict[str, Any]]:
        """Return the backward-compatible full V1 snapshot; never disclose V2."""
        self._runtime.ensure_schema()
        self._runtime.cleanup_expired()
        with self._runtime.connect() as conn:
            snapshots: list[dict[str, Any]] = []
            for session, _memberships_for_session, selected in self._account_v1_sessions_locked(
                conn,
                requester_user_id,
            ):
                snapshot = session_snapshot(conn, session, include_participants=True)
                local_peer_id = str(selected["peer_id"])
                snapshot["local_peer_ids"] = [local_peer_id]
                snapshot["local_peer_id"] = local_peer_id
                snapshot["local_role"] = "owner" if selected["owner"] else "participant"
                snapshots.append(snapshot)
            return snapshots

    def list_sessions_for_membership_proofs(
        self,
        *,
        requester_user_id: str,
        membership_proofs: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Return V1 compatibility rows plus exact proof-validated V2 rows."""
        self._runtime.ensure_schema()
        self._runtime.cleanup_expired()
        with self._runtime.connect() as conn:
            v1_items = [
                session_catalog_item(
                    session,
                    memberships,
                    selected,
                    signing_secret=self._runtime.config.RENDEZVOUS_SECURITY_SIGNING_SECRET,
                )
                for session, memberships, selected in self._account_v1_sessions_locked(
                    conn,
                    requester_user_id,
                )
            ]
            v2_items: list[dict[str, Any]] = []
            for proof in membership_proofs:
                session = get_session_by_id(conn, proof["session_id"])
                if (
                    not session
                    or session.get("revoked_at") is not None
                    or float(session.get("expires_at") or 0) <= self._runtime.clock()
                    or int(session.get("identity_binding_version") or 0) != 2
                ):
                    continue
                try:
                    selected = resolve_authenticated_membership(
                        conn,
                        session,
                        account_id=requester_user_id,
                        requested_peer_id=proof["local_peer_id"],
                        membership_capability=proof["membership_capability"],
                    )
                    memberships = validated_strict_memberships(conn, session, require_pair=False)
                except ValueError:
                    # A catalog batch is not a membership-validation oracle. One
                    # invalid proof does not reveal whether the session exists.
                    continue
                v2_items.append(
                    session_catalog_item(
                        session,
                        memberships,
                        selected,
                        signing_secret=self._runtime.config.RENDEZVOUS_SECURITY_SIGNING_SECRET,
                    )
                )
        return sorted(
            [*v1_items, *v2_items],
            key=lambda item: (float(item.get("created_at") or 0), str(item["id"])),
            reverse=True,
        )


__all__ = ["SessionCatalog"]
