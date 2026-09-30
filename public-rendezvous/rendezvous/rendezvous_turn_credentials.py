"""coturn REST credentials for current strict-pair members."""

from __future__ import annotations

import hashlib
from typing import Any

from rendezvous_membership import (
    require_transport_ready_locked,
    resolve_authenticated_membership,
    validated_strict_memberships,
)
from rendezvous_records import get_session_by_id
from rendezvous_runtime import RendezvousRuntime


class TurnCredentialIssuer:
    """Issue time-bound, pseudonymous TURN credentials."""

    def __init__(self, *, runtime: RendezvousRuntime) -> None:
        self._runtime = runtime

    def issue_turn_credentials(
        self,
        *,
        session_id: str,
        requester_user_id: str,
        requester_peer_id: str = "",
        membership_capability: str = "",
    ) -> dict[str, Any]:
        """Issue coturn REST credentials only for a current strict-pair member."""
        import base64
        import hmac

        self._runtime.ensure_schema()
        now = self._runtime.clock()
        with self._runtime.connect() as conn:
            session = get_session_by_id(conn, session_id)
            if not session:
                return {"ok": False, "reason": "session_not_found"}
            if session.get("revoked_at") is not None or float(session.get("expires_at") or 0) <= now:
                return {"ok": False, "reason": "session_inactive"}
            try:
                memberships = validated_strict_memberships(conn, session, require_pair=False)
                requester = resolve_authenticated_membership(
                    conn,
                    session,
                    account_id=requester_user_id,
                    requested_peer_id=requester_peer_id,
                    membership_capability=membership_capability,
                )
                require_transport_ready_locked(conn, session, memberships, clock=self._runtime.clock)
            except ValueError as exc:
                return {"ok": False, "reason": str(exc)}
            local_peer_id = str(requester["peer_id"])
            if local_peer_id not in {str(member["peer_id"]) for member in memberships}:
                return {"ok": False, "reason": "forbidden"}
            if len(memberships) != 2:
                return {"ok": False, "reason": "strict_pair_membership_incomplete"}

        secret = self._runtime.config.TURN_SHARED_SECRET
        if not secret or not self._runtime.config.TURN_URLS or self._runtime.config.TURN_TTL_SECONDS < 1:
            return {"ok": False, "reason": "turn_not_configured"}
        now_seconds = int(now)
        expiry = min(now_seconds + self._runtime.config.TURN_TTL_SECONDS, int(float(session["expires_at"])))
        ttl = expiry - now_seconds
        if ttl < 1:
            return {"ok": False, "reason": "session_inactive"}
        session_pseudonym = hmac.new(
            secret.encode(),
            b"ananta.turn.session.v1\0" + session_id.encode(),
            hashlib.sha256,
        ).hexdigest()[:24]
        peer_pseudonym = hmac.new(
            secret.encode(),
            b"ananta.turn.session-peer.v1\0" + session_id.encode() + b"\0" + local_peer_id.encode(),
            hashlib.sha256,
        ).hexdigest()[:24]
        username = f"{expiry}:session-{session_pseudonym}:peer-{peer_pseudonym}"
        key = hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()
        password = base64.b64encode(key).decode()
        self._runtime.logger.info(
            "turn_credentials_issued session=%s peer=%s expires_at=%d",
            session_pseudonym,
            peer_pseudonym,
            expiry,
        )
        return {
            "ok": True,
            "credentials": {
                "username": username,
                "password": password,
                "ttl": ttl,
                "expires_at": expiry,
                "uris": list(self._runtime.config.TURN_URLS),
                "session_id": session_id,
                "local_peer_id": local_peer_id,
            },
        }


__all__ = ["TurnCredentialIssuer"]
