"""Hub composition root for the shared semantic relay."""

from __future__ import annotations

import os
import threading

from agent.repositories.semantic_relay_shared_store import SharedSemanticRelayRepository
from agent.repositories.webrtc_peer_key_repository import WebrtcPeerKeyRepository
from agent.services.semantic_media_audit_service import SemanticMediaAuditPort
from agent.services.semantic_media_feature_flags import resolve_semantic_media_feature_flags
from agent.services.semantic_relay_authorization import SemanticRelayAuthorization
from agent.services.semantic_relay_service import SemanticRelayService
from agent.services.share_session_relay_membership import ShareSessionRelayMembership
from agent.services.share_session_service import get_share_session_service
from agent.services.webrtc_epoch_service import get_webrtc_epoch_service

_SERVICE: SemanticRelayService | None = None
_AUDIT: SemanticMediaAuditPort | None = None
_LOCK = threading.Lock()

_SECURITY_TRAFFIC_CLASS = {
    "control": "control",
    "transcript": "semantic",
    "audio_recovery": "semantic",
    "visual_semantic": "semantic",
    "evidence_bulk": "bulk",
    "diagnostic": "control",
}


class _WebrtcReplayAdapter:
    def decide(
        self,
        *,
        session_id: str,
        epoch: int,
        sender_id: str,
        traffic_class: str,
        sequence: int,
    ) -> tuple[bool, str]:
        mapped = _SECURITY_TRAFFIC_CLASS.get(traffic_class)
        if mapped is None:
            return False, "relay_traffic_class_unknown"
        decision = get_webrtc_epoch_service().accept_sequence(
            scope_kind="session",
            scope_id=session_id,
            epoch=epoch,
            sender_id=sender_id,
            authenticated_sender_id=sender_id,
            traffic_class=mapped,
            sequence=sequence,
        )
        return decision.accepted, decision.reason_code


class _EnvironmentTrafficPolicy:
    @staticmethod
    def enabled(traffic_class: str) -> bool:
        flags = resolve_semantic_media_feature_flags(os.environ)
        if traffic_class in {"control", "diagnostic"}:
            return True
        if traffic_class == "visual_semantic":
            return flags.get("semantic_visual_capture", False)
        if traffic_class in {"transcript", "audio_recovery"}:
            return flags.get("semantic_speech_runtime", False)
        if traffic_class == "evidence_bulk":
            return flags.get("peer_evidence_sync", False)
        return False


class _PeerKeyConfirmationAdapter:
    def __init__(self) -> None:
        self._repository = WebrtcPeerKeyRepository()

    def confirmed(
        self,
        *,
        session_id: str,
        epoch: int,
        sender_id: str,
        audience_id: str,
        now: float,
    ) -> bool:
        return (
            self._repository.get_confirmation(
                scope_id=session_id,
                epoch=epoch,
                sender_peer_id=sender_id,
                recipient_peer_id=audience_id,
                now=now,
            )
            is not None
        )


def get_semantic_relay_service() -> SemanticRelayService:
    global _SERVICE
    if _SERVICE is None:
        with _LOCK:
            if _SERVICE is None:
                membership = ShareSessionRelayMembership(
                    get_share_session_service(),
                    epoch_resolver=lambda session_id: get_webrtc_epoch_service().current_epoch("session", session_id),
                )
                _SERVICE = SemanticRelayService(
                    repository=SharedSemanticRelayRepository(),
                    authorization=SemanticRelayAuthorization(membership),
                    replay=_WebrtcReplayAdapter(),
                    traffic_policy=_EnvironmentTrafficPolicy(),
                    key_confirmation=_PeerKeyConfirmationAdapter(),
                    audit=_AUDIT,
                )
    return _SERVICE


def reset_semantic_relay_service() -> None:
    global _SERVICE
    with _LOCK:
        _SERVICE = None


def configure_semantic_relay_audit(audit: SemanticMediaAuditPort) -> None:
    """Bind the Hub audit port before the lazily composed relay is used."""

    global _AUDIT, _SERVICE
    with _LOCK:
        _AUDIT = audit
        # Recompose if a development/test caller resolved the singleton before
        # Flask bootstrap.  Persisted relay state remains in the SQL adapter.
        _SERVICE = None


__all__ = [
    "configure_semantic_relay_audit",
    "get_semantic_relay_service",
    "reset_semantic_relay_service",
]
