"""Share-session authority adapters for bilateral speech-evidence sync.

Extracted from ``agent.services.speech_evidence_sync_composition`` (SRP): these
adapters project current strict-E2EE share membership and WebRTC session epochs
into the narrow authority ports consumed by the offer and sync services.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Mapping

from agent.services.share_session_relay_membership import ShareSessionRelayMembership
from agent.services.share_session_service import ShareSessionService
from agent.services.speech_evidence_offer_service import HubPeerAuthorization
from agent.services.webrtc_epoch_service import WebrtcEpochService


class ShareSessionSpeechEvidenceMembership:
    """Resolve pair authority from current strict-E2EE share membership.

    Browser key bindings use the share scope as both ``session_id`` and
    ``pair_id``.  Keeping that invariant at the Hub prevents a member from
    inventing an independent pair namespace inside an authorized session.
    """

    def __init__(
        self,
        sessions: ShareSessionService,
        epochs: WebrtcEpochService,
        *,
        clock=time.time,
    ) -> None:
        self._sessions = sessions
        self._epochs = epochs
        self._clock = clock
        self._relay_membership = ShareSessionRelayMembership(
            sessions,
            epoch_resolver=lambda session_id: epochs.current_epoch("session", session_id),
            clock=clock,
        )

    def current(
        self,
        *,
        session_id: str,
        pair_id: str,
        peer_id: str,
        audience_id: str,
    ) -> HubPeerAuthorization | None:
        if pair_id != session_id or peer_id == audience_id:
            return None
        share = self._sessions.get_session(session_id)
        if not isinstance(share, dict):
            return None
        if (
            share.get("revoked_at") is not None
            or share.get("security_mode") != "strict_e2ee"
            or int(share.get("security_contract_version") or 0) != 1
        ):
            return None
        expires_at = share.get("expires_at")
        if isinstance(expires_at, (int, float)) and float(expires_at) <= float(self._clock()):
            return None
        tenant_id = str(share.get("tenant_id") or "default")
        sender = self._relay_membership.member(
            tenant_id=tenant_id,
            session_id=session_id,
            member_id=peer_id,
        )
        audience = self._relay_membership.member(
            tenant_id=tenant_id,
            session_id=session_id,
            member_id=audience_id,
        )
        if (
            sender is None
            or audience is None
            or sender.epoch != audience.epoch
            or audience_id not in sender.send_audiences
            or "peer_evidence_sync" not in sender.permissions
            or "peer_evidence_sync" not in audience.permissions
        ):
            return None
        return HubPeerAuthorization(
            tenant_id=tenant_id,
            session_id=session_id,
            pair_id=pair_id,
            peer_id=peer_id,
            audience_id=audience_id,
            epoch=sender.epoch,
            membership_version=self._membership_version(share, peer_id),
            permissions=frozenset({"peer_evidence_sync"}),
            active=True,
        )

    def _membership_version(self, share: Mapping[str, Any], peer_id: str) -> int:
        participants = self._sessions.get_participants(str(share.get("id") or ""))
        participant = next(
            (row for row in participants if str(row.get("user_id") or "") == peer_id and row.get("revoked_at") is None),
            None,
        )
        basis = {
            "peer_id": peer_id,
            "owner": str(share.get("owner_user_id") or ""),
            "created_at": share.get("created_at"),
            "session_permissions": share.get("permissions"),
            "participant_id": participant.get("id") if participant else None,
            "joined_at": participant.get("joined_at") if participant else None,
            "participant_permissions": participant.get("permissions") if participant else None,
        }
        digest = hashlib.sha256(
            json.dumps(basis, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).digest()
        return int.from_bytes(digest[:4], "big") % 2_147_483_646 + 1


class ShareSessionSpeechEvidenceEpoch:
    def __init__(self, epochs: WebrtcEpochService) -> None:
        self._epochs = epochs

    def current_epoch(self, *, session_id: str, pair_id: str) -> int | None:
        if pair_id != session_id:
            return None
        return self._epochs.current_epoch("session", session_id)


__all__ = [
    "ShareSessionSpeechEvidenceEpoch",
    "ShareSessionSpeechEvidenceMembership",
]
