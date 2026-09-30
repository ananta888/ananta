"""Shared request identity, membership and strict-E2EE helpers of the share-session routes.

The process-local route state (rate limiter, participant presence, view-start
audit markers, peer key confirmations and the secure envelope validator) lives
here once so every share-session route module works on the same instances.
"""

from __future__ import annotations

import time
from typing import Any

from flask import request

from agent.auth import get_request_auth_context
from agent.services.rate_limit_service import RateLimitService
from agent.services.share_security_negotiation_service import (
    get_share_security_negotiation_service,
)
from agent.services.share_session_permissions import (
    get_share_session_permission_service,
)
from agent.services.share_session_service import get_share_session_service
from agent.services.share_view_security_service import (
    ShareSecureEnvelopeService,
    ShareViewSecurityError,
)
from agent.services.webrtc_epoch_service import get_webrtc_epoch_service
from agent.services.webrtc_peer_identity_service import (
    derive_peer_key_package_id,
)
from agent.services.webrtc_peer_key_confirmation_service import (
    get_webrtc_peer_key_confirmation_service,
)
from ananta_contracts.webrtc_security import SecureEnvelopeV1


_rate_limiter = RateLimitService()


_view_started_audited: set[str] = set()
_participant_last_seen: dict[str, float] = {}  # participant_id -> timestamp
_peer_key_repository = get_webrtc_peer_key_confirmation_service()
_share_envelope_security = ShareSecureEnvelopeService(get_webrtc_epoch_service())


_STRICT_VIEW_TRAFFIC = {
    "pair.view_delta": "semantic",
    "pair.cursor": "control",
    "pair.control": "control",
    "pair.snapshot_request": "control",
    "pair.artifact_ref": "semantic",
}
_STRICT_VIEW_PERMISSIONS = {
    "pair.view_delta": "view_tui",
    "pair.cursor": "remote_cursor",
    "pair.control": "remote_control",
    "pair.snapshot_request": "view_tui",
    "pair.artifact_ref": "artifact_share",
}


def _is_session_active(session_item: dict[str, Any]) -> bool:
    if not isinstance(session_item, dict):
        return False
    if session_item.get("revoked_at") is not None:
        return False
    exp = session_item.get("expires_at")
    if isinstance(exp, (int, float)) and float(exp) <= time.time():
        return False
    return True


def _is_active_participant(*, session_id: str, user_id: str, session_item: dict[str, Any] | None = None) -> bool:
    if not user_id:
        return False
    service = get_share_session_service()
    session = session_item if isinstance(session_item, dict) else service.get_session(session_id)
    if not isinstance(session, dict) or not _is_session_active(session):
        return False
    if str(session.get("owner_user_id") or "") == user_id:
        return True
    participants = service.get_participants(session_id)
    return any(str(p.get("user_id") or "") == user_id and not p.get("revoked_at") for p in participants)


def _current_user_id() -> str:
    auth = dict(get_request_auth_context() or {})
    return str(auth.get("sub") or auth.get("username") or "").strip()


def _current_device_id() -> str:
    raw = request.headers.get("X-Ananta-Device-Id")
    return str(raw or "").strip()


def _current_tenant_id() -> str:
    auth = dict(get_request_auth_context() or {})
    return str(auth.get("tenant_id") or auth.get("tenant") or "default")[:128]


def _strict_e2ee_enabled(session_item: dict[str, Any]) -> bool:
    metadata = dict(session_item.get("session_metadata") or {})
    version = int(session_item.get("security_contract_version") or metadata.get("security_contract_version") or 0)
    mode = str(session_item.get("security_mode") or metadata.get("security_mode") or "legacy")
    return version == 1 and mode == "strict_e2ee"


def _strict_security_contract(
    *,
    session_id: str,
    session_item: dict[str, Any],
    epoch: int,
    memberships: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return get_share_security_negotiation_service().finalize_strict_pair(
        session_id=session_id,
        tenant_id=str(session_item.get("tenant_id") or "default")[:128],
        epoch=epoch,
        owner_peer_id=str(session_item.get("owner_user_id") or ""),
        memberships=memberships
        if memberships is not None
        else get_share_session_service().get_security_memberships(session_id),
        session_expires_at=session_item.get("expires_at"),
    )


def _strict_pair_authorizer(
    *,
    session_id: str,
    session_item: dict[str, Any],
    permission_by_payload: dict[str, str],
    contract_digest: str,
):
    service = get_share_session_service()
    memberships = [
        item for item in service.get_security_memberships(session_id) if item.get("active")
    ]
    member_by_peer = {str(item.get("peer_id") or ""): item for item in memberships}
    active_peers = set(member_by_peer)

    def authorize(secure: SecureEnvelopeV1) -> None:
        if secure.sender_id not in active_peers or secure.recipient.id not in active_peers:
            raise ShareViewSecurityError("recipient_membership_stale", status_code=403)
        required_permission = permission_by_payload.get(secure.payload_type)
        if not required_permission or not get_share_session_permission_service().allows(
            session_id, session_item.get("permissions"), required_permission
        ):
            raise ShareViewSecurityError("payload_permission_required", status_code=403)
        now = time.time()
        forward = _peer_key_repository.get_confirmation(
            scope_id=session_id,
            epoch=secure.epoch,
            sender_peer_id=secure.sender_id,
            recipient_peer_id=secure.recipient.id,
            now=now,
        )
        reverse = _peer_key_repository.get_confirmation(
            scope_id=session_id,
            epoch=secure.epoch,
            sender_peer_id=secure.recipient.id,
            recipient_peer_id=secure.sender_id,
            now=now,
        )
        if forward is None or reverse is None:
            raise ShareViewSecurityError("bidirectional_key_confirmation_required", status_code=409)
        expected_forward = _expected_peer_package_id(
            remote_member=member_by_peer[secure.recipient.id],
            recipient_peer_id=secure.sender_id,
            epoch=secure.epoch,
            contract_digest=contract_digest,
        )
        expected_reverse = _expected_peer_package_id(
            remote_member=member_by_peer[secure.sender_id],
            recipient_peer_id=secure.recipient.id,
            epoch=secure.epoch,
            contract_digest=contract_digest,
        )
        if forward.package_id != expected_forward or reverse.package_id != expected_reverse:
            raise ShareViewSecurityError("key_confirmation_binding_stale", status_code=409)

    return authorize


def _expected_peer_package_id(
    *,
    remote_member: dict[str, Any],
    recipient_peer_id: str,
    epoch: int,
    contract_digest: str,
) -> str:
    return derive_peer_key_package_id(
        membership_id=str(remote_member.get("membership_id") or ""),
        membership_version=int(remote_member.get("membership_version") or 0),
        recipient_peer_id=recipient_peer_id,
        epoch=epoch,
        device_key_fingerprint=str(remote_member.get("fingerprint") or ""),
        security_contract_digest=contract_digest,
    )
