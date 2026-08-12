from __future__ import annotations

import base64
import binascii
import hashlib
import time
import uuid
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from agent.auth import check_user_auth, get_request_auth_context
from agent.config import settings
from agent.repositories.semantic_relay_repository import SemanticRelayRepositoryError
from agent.repositories.webrtc_peer_key_repository import (
    WebrtcPeerKeyRepository,
    WebrtcPeerKeyRepositoryError,
)
from agent.services.rate_limit_service import RateLimitService
from agent.services.semantic_media_audit_service import SemanticMediaAuditError
from agent.services.semantic_relay_authorization import SemanticRelayAuthorizationError
from agent.services.semantic_relay_composition import get_semantic_relay_service
from agent.services.semantic_relay_service import SemanticRelayServiceError
from agent.services.share_audit_service import (
    audit_chat_sent,
    audit_participant_joined,
    audit_participant_revoked,
    audit_permission_changed,
    audit_session_created,
    audit_view_delta_sent,
    audit_view_started,
)
from agent.services.share_relay_compatibility_service import (
    ShareRelayCompatibilityError,
    get_share_relay_compatibility_service,
)
from agent.services.share_security_negotiation_service import (
    ShareSecurityNegotiationError,
    get_share_security_negotiation_service,
)
from agent.services.share_session_permissions import (
    PermissionContractError,
    get_share_session_permission_service,
)
from agent.services.share_session_service import get_share_session_service
from agent.services.share_view_security_service import (
    ShareSecureEnvelopeService,
    ShareViewSecurityError,
)
from agent.services.webrtc_epoch_service import get_webrtc_epoch_service
from agent.services.webrtc_peer_identity_service import (
    PeerIdentityError,
    PeerMembership,
    WebrtcPeerIdentityService,
    derive_hub_identity_key,
    derive_peer_key_package_id,
)
from ananta_contracts.webrtc_datachannel import DataChannelContractError
from ananta_contracts.webrtc_security import SecureEnvelopeV1

share_sessions_bp = Blueprint("share_sessions", __name__)
_rate_limiter = RateLimitService()

_CHAT_QUEUE_MAX = 200
_CHAT_MSG_MAX_BYTES = 64 * 1024
_VIEW_QUEUE_MAX = 50
_VIEW_PAYLOAD_MAX_BYTES = 256 * 1024
_VIEW_FRAME_RATE = {"namespace": "share_view_push", "limit": 40, "window_seconds": 10}
_VIEW_POLL_RATE = {"namespace": "share_view_poll", "limit": 80, "window_seconds": 10}
_CHAT_SEND_RATE = {"namespace": "share_chat_send", "limit": 60, "window_seconds": 10}
_CHAT_POLL_RATE = {"namespace": "share_chat_poll", "limit": 120, "window_seconds": 10}

_view_started_audited: set[str] = set()
_participant_last_seen: dict[str, float] = {}  # participant_id -> timestamp
_peer_key_repository = WebrtcPeerKeyRepository()
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


def _semantic_relay_error(exc: Exception):
    if isinstance(exc, DataChannelContractError):
        return jsonify({"error": exc.reason_code, "field": exc.field or None}), exc.status_code
    if isinstance(exc, SemanticRelayServiceError):
        return jsonify({"error": exc.reason_code}), exc.status_code
    if isinstance(exc, SemanticRelayAuthorizationError):
        status = 409 if exc.reason_code == "relay_epoch_stale" else 403
        return jsonify({"error": exc.reason_code}), status
    if isinstance(exc, SemanticRelayRepositoryError):
        status = 409 if exc.reason_code == "relay_message_id_conflict" else 429
        return jsonify({"error": exc.reason_code}), status
    raise exc


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


def _peer_identity_service(
    *, session_id: str, tenant_id: str, memberships: list[dict[str, Any]]
) -> WebrtcPeerIdentityService:
    indexed = {str(item.get("membership_id") or ""): item for item in memberships}

    def membership_lookup(membership_id: str) -> PeerMembership | None:
        item = indexed.get(membership_id)
        if not item:
            return None
        return PeerMembership(
            membership_id=membership_id,
            tenant_id=tenant_id,
            scope_kind="session",
            scope_id=session_id,
            peer_id=str(item.get("peer_id") or ""),
            device_id=str(item.get("device_id") or ""),
            membership_version=int(item.get("membership_version") or 1),
            active=bool(item.get("active")),
        )

    def fingerprint_lookup(peer_id: str, device_id: str) -> str | None:
        for item in memberships:
            if item.get("peer_id") == peer_id and item.get("device_id") == device_id and item.get("active"):
                return str(item.get("fingerprint") or "") or None
        return None

    private_key = derive_hub_identity_key(str(settings.secret_key).encode("utf-8"))
    public_key_b64 = base64.b64encode(private_key.public_key().public_bytes_raw()).decode("ascii")
    return WebrtcPeerIdentityService(
        private_key,
        hub_key_id=f"hub-ed25519:{hashlib.sha256(public_key_b64.encode()).hexdigest()[:16]}",
        membership_lookup=membership_lookup,
        device_fingerprint_lookup=fingerprint_lookup,
    )


@share_sessions_bp.route("/share-sessions", methods=["POST"])
@check_user_auth
def create_share_session():
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    owner_device_id = str(body.get("owner_device_id") or _current_device_id() or f"web-{user_id[:16]}").strip()
    service = get_share_session_service()
    try:
        session_item = service.create_session(
            owner_user_id=user_id,
            owner_device_id=owner_device_id,
            title=str(body.get("title") or "Shared Session").strip() or "Shared Session",
            mode=str(body.get("mode") or "relay").strip() or "relay",
            transport=str(body.get("transport") or "hub_relay").strip() or "hub_relay",
            permissions=body.get("permissions") if body.get("permissions") is not None else {},
            expires_at=float(body["expires_at"]) if isinstance(body.get("expires_at"), (int, float)) else None,
            security_contract_version=body.get("security_contract_version", 0),
            security_mode=str(body.get("security_mode") or "legacy"),
            owner_public_key_spki_b64=str(body.get("public_key_spki_b64") or ""),
            owner_public_key_fingerprint=str(body.get("public_key_fingerprint") or ""),
            tenant_id=_current_tenant_id(),
        )
    except (PermissionContractError, PeerIdentityError) as exc:
        return jsonify({"error": exc.reason_code, "field": getattr(exc, "field", None)}), 400
    audit_session_created(
        session_id=str(session_item.get("id") or ""),
        owner_user_id=user_id,
        owner_device_id=owner_device_id,
        mode=str(session_item.get("mode") or ""),
        transport=str(session_item.get("transport") or ""),
        permissions=dict(session_item.get("permissions") or {}),
    )
    return jsonify({"ok": True, "session": session_item, "data": session_item}), 201


@share_sessions_bp.route("/share-sessions", methods=["GET"])
@check_user_auth
def list_share_sessions():
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    service = get_share_session_service()
    items = service.list_sessions_for_owner(user_id)
    return jsonify({"ok": True, "sessions": items, "data": {"items": items}}), 200


@share_sessions_bp.route("/share-sessions/joined", methods=["GET"])
@check_user_auth
def list_joined_share_sessions():
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    service = get_share_session_service()
    items = service.list_sessions_as_participant(user_id)
    return jsonify({"ok": True, "sessions": items, "data": {"items": items}}), 200


@share_sessions_bp.route("/share-sessions/join-by-code", methods=["POST"])
@check_user_auth
def join_share_session_by_code():
    """Join a session using only an invite_code — no session_id required."""
    auth = dict(get_request_auth_context() or {})
    user_id = str(auth.get("sub") or auth.get("username") or "").strip()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    invite_code = str(body.get("invite_code") or "").strip()
    if not invite_code:
        return jsonify({"error": "invite_code_required"}), 400
    device_id = str(body.get("device_id") or _current_device_id() or f"web-{user_id[:16]}").strip()
    fingerprint = str(body.get("public_key_fingerprint") or "").strip()
    public_key_spki_b64 = str(body.get("public_key_spki_b64") or "").strip()
    service = get_share_session_service()
    session_item = service.get_session_by_invite_code(invite_code)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    minimum_security_mode = str(body.get("minimum_security_mode") or "legacy")
    if minimum_security_mode not in {"legacy", "strict_e2ee"}:
        return jsonify({"error": "minimum_security_mode_invalid"}), 400
    if minimum_security_mode == "strict_e2ee" and not _strict_e2ee_enabled(session_item):
        return jsonify({"error": "security_downgrade_rejected"}), 409
    session_id = str(session_item.get("id") or "")
    joined = service.join_session(
        session_id=session_id,
        user_id=user_id,
        device_id=device_id,
        public_key_fingerprint=fingerprint,
        invite_code=invite_code,
        public_key_spki_b64=public_key_spki_b64,
        tenant_id=_current_tenant_id(),
    )
    if not joined.ok:
        code_map = {
            "session_not_found": 404,
            "invalid_invite": 403,
            "session_revoked": 403,
            "session_expired": 403,
            "cross_tenant_denied": 403,
        }
        return jsonify({"error": joined.reason or "join_failed"}), code_map.get(joined.reason or "", 400)
    participant = dict(joined.participant or {})
    _participant_last_seen[str(participant.get("id") or "")] = time.time()
    audit_participant_joined(
        session_id=session_id,
        participant_id=str(participant.get("id") or ""),
        user_id=user_id,
        device_id=str(participant.get("device_id") or ""),
        public_key_fingerprint=str(participant.get("public_key_fingerprint") or ""),
        permissions=dict(participant.get("permissions") or {}),
    )
    # Membership changes rotate the authoritative epoch; return the refreshed
    # session rather than the pre-join snapshot.
    session_item = service.get_session(session_id) or session_item
    return jsonify({"ok": True, "session": session_item, "participant": participant}), 201


@share_sessions_bp.route("/share-sessions/<session_id>/participants", methods=["GET"])
@check_user_auth
def list_share_session_participants(session_id: str):
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant"}), 403
    raw = service.get_participants(session_id)
    participants = []
    for p in raw:
        entry = dict(p)
        entry["last_seen_at"] = _participant_last_seen.get(str(p.get("id") or ""))
        participants.append(entry)
    # Include owner as synthetic participant entry
    owner_id = str(session_item.get("owner_user_id") or "")
    if owner_id and not any(str(p.get("user_id") or "") == owner_id for p in raw):
        participants.insert(
            0,
            {
                "id": f"owner-{owner_id}",
                "user_id": owner_id,
                "device_id": str(session_item.get("owner_device_id") or ""),
                "role": "owner",
                "permissions": dict(session_item.get("permissions") or {}),
                "joined_at": float(session_item.get("created_at") or 0),
                "revoked_at": None,
                "last_seen_at": _participant_last_seen.get(f"owner-{owner_id}"),
            },
        )
    return jsonify({"ok": True, "participants": participants}), 200


@share_sessions_bp.route("/share-sessions/<session_id>/security/key-packages", methods=["GET"])
@check_user_auth
def list_share_session_key_packages(session_id: str):
    """Return Hub-signed packages addressed to the authenticated member."""
    user_id = _current_user_id()
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_session_active(session_item) or not _is_active_participant(
        session_id=session_id, user_id=user_id, session_item=session_item
    ):
        return jsonify({"error": "not_a_participant"}), 403
    if not _strict_e2ee_enabled(session_item):
        return jsonify({"error": "strict_e2ee_not_enabled"}), 409
    epoch = get_webrtc_epoch_service().current_epoch("session", session_id)
    if epoch is None:
        return jsonify({"error": "security_epoch_unavailable"}), 409
    memberships = service.get_security_memberships(session_id)
    local_members = [m for m in memberships if m.get("active") and m.get("peer_id") == user_id]
    if not local_members:
        return jsonify({"error": "membership_stale"}), 403
    tenant_id = str(session_item.get("tenant_id") or "default")[:128]
    if tenant_id != _current_tenant_id():
        return jsonify({"error": "cross_tenant_denied"}), 403
    identity = _peer_identity_service(session_id=session_id, tenant_id=tenant_id, memberships=memberships)
    remote_members = [
        member for member in memberships if member.get("active") and member.get("peer_id") != user_id
    ]
    if not remote_members:
        return jsonify(
            {
                "ok": True,
                "epoch": epoch,
                "tenant_id": tenant_id,
                "security_contract_digest": None,
                "security_contract": None,
                "hub_key_id": identity.hub_key_id,
                "hub_public_key_b64": identity.hub_public_key_b64(),
                "packages": [],
            }
        ), 200
    active_memberships = [member for member in memberships if member.get("active")]
    try:
        if len(active_memberships) == 2:
            contract = _strict_security_contract(
                session_id=session_id,
                session_item=session_item,
                epoch=epoch,
                memberships=memberships,
            )
        else:
            contract = get_share_security_negotiation_service().finalize_strict_group(
                session_id=session_id,
                tenant_id=tenant_id,
                epoch=epoch,
                owner_peer_id=str(session_item.get("owner_user_id") or ""),
                memberships=memberships,
                session_expires_at=session_item.get("expires_at"),
            )
    except ShareSecurityNegotiationError as exc:
        return jsonify({"error": exc.reason_code}), exc.status_code
    contract_digest = str(contract["digest"])
    packages: list[dict[str, Any]] = []
    for member in memberships:
        if not member.get("active") or member.get("peer_id") == user_id:
            continue
        public_key = str(member.get("public_key_spki_b64") or "")
        if not public_key:
            return jsonify({"error": "peer_device_key_missing"}), 409
        try:
            package = identity.issue_key_package(
                membership_id=str(member.get("membership_id") or ""),
                recipient_peer_id=user_id,
                epoch=epoch,
                ecdh_public_key_spki_b64=public_key,
                security_contract_digest=contract_digest,
                expires_at_ms=int((time.time() + 300) * 1000),
            )
        except PeerIdentityError as exc:
            return jsonify({"error": exc.reason_code}), 409
        packages.append(package.__dict__)
    return jsonify(
        {
            "ok": True,
            "epoch": epoch,
            "tenant_id": tenant_id,
            "security_contract_digest": contract_digest,
            "security_contract": contract,
            "hub_key_id": identity.hub_key_id,
            "hub_public_key_b64": identity.hub_public_key_b64(),
            "packages": packages,
        }
    ), 200


@share_sessions_bp.route("/share-sessions/<session_id>/security/key-confirmations", methods=["POST"])
@check_user_auth
def put_share_session_key_confirmation(session_id: str):
    user_id = _current_user_id()
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _strict_e2ee_enabled(session_item):
        return jsonify({"error": "strict_e2ee_required"}), 409
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant"}), 403
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    if set(body) != {"recipient_peer_id", "package_id", "epoch", "confirmation_tag"}:
        return jsonify({"error": "key_confirmation_fields_invalid"}), 400
    recipient_peer_id = str(body.get("recipient_peer_id") or "")
    package_id = str(body.get("package_id") or "")
    tag = str(body.get("confirmation_tag") or "")
    epoch = body.get("epoch")
    current_epoch = get_webrtc_epoch_service().current_epoch("session", session_id)
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch != current_epoch:
        return jsonify({"error": "epoch_mismatch"}), 409
    memberships = service.get_security_memberships(session_id)
    active_memberships = [item for item in memberships if item.get("active")]
    local_members = [item for item in active_memberships if str(item.get("peer_id") or "") == user_id]
    remote_members = [
        item for item in active_memberships if str(item.get("peer_id") or "") == recipient_peer_id
    ]
    if len(local_members) != 1 or len(remote_members) != 1 or recipient_peer_id == user_id:
        return jsonify({"error": "recipient_mismatch"}), 403
    if len(package_id) != 64 or any(char not in "0123456789abcdef" for char in package_id):
        return jsonify({"error": "package_id_invalid"}), 400
    try:
        decoded_tag = base64.b64decode(tag, validate=True)
    except (ValueError, binascii.Error):
        return jsonify({"error": "confirmation_tag_invalid"}), 400
    if len(decoded_tag) != 32:
        return jsonify({"error": "confirmation_tag_invalid"}), 400
    try:
        contract = _strict_security_contract(
            session_id=session_id,
            session_item=session_item,
            epoch=epoch,
            memberships=memberships,
        )
    except ShareSecurityNegotiationError as exc:
        return jsonify({"error": exc.reason_code}), exc.status_code
    remote = remote_members[0]
    expected_package_id = _expected_peer_package_id(
        remote_member=remote,
        recipient_peer_id=user_id,
        epoch=epoch,
        contract_digest=str(contract["digest"]),
    )
    if package_id != expected_package_id:
        return jsonify({"error": "key_package_binding_mismatch"}), 409
    now = time.time()
    audit = current_app.extensions.get("semantic_media_audit_recorder")
    if audit is None:
        return jsonify({"error": "security_audit_unavailable"}), 503
    try:
        audit_event = audit.prepare_transition(
            idempotency_key=(
                f"pair-key-confirm:{session_id}:{epoch}:{user_id}:{recipient_peer_id}:"
                f"{package_id}:{int(now // 120)}"
            ),
            tenant_id=str(session_item.get("tenant_id") or "default"),
            scope=f"session:{session_id}",
            event_type="semantic_rekey",
            transition="pair_key_confirmation",
            reason_code="confirmed",
            epoch=epoch,
            contract_ref=str(contract["digest"]),
        )
        _peer_key_repository.put_confirmation(
            scope_id=session_id,
            epoch=epoch,
            sender_peer_id=user_id,
            recipient_peer_id=recipient_peer_id,
            package_id=package_id,
            confirmation_tag=tag,
            expires_at=now + 300,
            now=now,
            audit_event=audit_event,
        )
    except (SemanticMediaAuditError, WebrtcPeerKeyRepositoryError) as exc:
        status = getattr(exc, "status_code", 409)
        return jsonify({"error": exc.reason_code}), status
    return jsonify({"ok": True}), 201


@share_sessions_bp.route("/share-sessions/<session_id>/security/key-confirmations", methods=["GET"])
@check_user_auth
def get_share_session_key_confirmation(session_id: str):
    user_id = _current_user_id()
    sender_peer_id = str(request.args.get("sender_peer_id") or "")
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _strict_e2ee_enabled(session_item):
        return jsonify({"error": "strict_e2ee_required"}), 409
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant"}), 403
    epoch = get_webrtc_epoch_service().current_epoch("session", session_id)
    if epoch is None:
        return jsonify({"error": "security_epoch_unavailable"}), 409
    active_peers = {
        str(item.get("peer_id") or "")
        for item in service.get_security_memberships(session_id)
        if item.get("active")
    }
    if sender_peer_id == user_id or sender_peer_id not in active_peers:
        return jsonify({"error": "sender_mismatch"}), 403
    row = _peer_key_repository.get_confirmation(
        scope_id=session_id,
        epoch=epoch,
        sender_peer_id=sender_peer_id,
        recipient_peer_id=user_id,
        now=time.time(),
    )
    if row is None:
        return jsonify({"ok": True, "confirmation": None}), 200
    return jsonify(
        {
            "ok": True,
            "confirmation": {
                "sender_peer_id": row.sender_peer_id,
                "recipient_peer_id": row.recipient_peer_id,
                "package_id": row.package_id,
                "epoch": row.epoch,
                "confirmation_tag": row.confirmation_tag,
                "expires_at": row.expires_at,
            },
        }
    ), 200


@share_sessions_bp.route("/share-sessions/<session_id>/semantic-relay", methods=["POST"])
@check_user_auth
def push_semantic_relay_envelope(session_id: str):
    """Persist one opaque, bilateral, epoch-bound DataChannel envelope."""

    user_id = _current_user_id()
    tenant_id = _current_tenant_id()
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_session_active(session_item):
        return jsonify({"error": "session_not_active"}), 403
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant"}), 403
    if not _strict_e2ee_enabled(session_item):
        return jsonify({"error": "strict_e2ee_required"}), 409
    raw = request.get_data(cache=True, as_text=False)
    try:
        stored = get_semantic_relay_service().append_wire(
            tenant_id=tenant_id,
            authenticated_sender_id=user_id,
            expected_session_id=session_id,
            raw=raw,
        )
    except (
        DataChannelContractError,
        SemanticRelayAuthorizationError,
        SemanticRelayRepositoryError,
        SemanticRelayServiceError,
    ) as exc:
        return _semantic_relay_error(exc)
    return jsonify(
        {
            "ok": True,
            "message_id": stored["message_id"],
            "cursor": stored["cursor"],
            "traffic_class": stored["traffic_class"],
        }
    ), 201


@share_sessions_bp.route("/share-sessions/<session_id>/semantic-relay", methods=["GET"])
@check_user_auth
def poll_semantic_relay_envelopes(session_id: str):
    user_id = _current_user_id()
    tenant_id = _current_tenant_id()
    share_service = get_share_session_service()
    session_item = share_service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_session_active(session_item):
        return jsonify({"error": "session_not_active"}), 403
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant"}), 403
    if not _strict_e2ee_enabled(session_item):
        return jsonify({"error": "strict_e2ee_required"}), 409
    traffic_class = str(request.args.get("traffic_class") or "")
    current_epoch = get_webrtc_epoch_service().current_epoch("session", session_id)
    try:
        epoch = int(request.args.get("epoch") or current_epoch or 0)
        cursor = int(request.args.get("cursor") or 0)
        limit = int(request.args.get("limit") or 50)
    except (TypeError, ValueError):
        return jsonify({"error": "relay_query_invalid"}), 400
    if current_epoch is None or epoch != current_epoch:
        return jsonify({"error": "relay_epoch_stale"}), 409
    try:
        page = get_semantic_relay_service().read_after(
            tenant_id=tenant_id,
            audience_id=user_id,
            session_id=session_id,
            epoch=epoch,
            traffic_class=traffic_class,
            cursor=cursor,
            limit=limit,
        )
    except (SemanticRelayAuthorizationError, SemanticRelayServiceError) as exc:
        return _semantic_relay_error(exc)
    return jsonify({"ok": True, **page}), 200


@share_sessions_bp.route("/share-sessions/<session_id>/semantic-relay/ack", methods=["POST"])
@check_user_auth
def acknowledge_semantic_relay_envelopes(session_id: str):
    user_id = _current_user_id()
    tenant_id = _current_tenant_id()
    share_service = get_share_session_service()
    session_item = share_service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant"}), 403
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    traffic_class = str(body.get("traffic_class") or "")
    current_epoch = get_webrtc_epoch_service().current_epoch("session", session_id)
    epoch = body.get("epoch")
    cursor = body.get("cursor")
    if (
        not isinstance(epoch, int)
        or isinstance(epoch, bool)
        or epoch != current_epoch
        or not isinstance(cursor, int)
        or isinstance(cursor, bool)
        or cursor < 0
    ):
        return jsonify({"error": "relay_ack_invalid"}), 400
    try:
        acknowledged = get_semantic_relay_service().acknowledge(
            tenant_id=tenant_id,
            audience_id=user_id,
            session_id=session_id,
            epoch=epoch,
            traffic_class=traffic_class,
            cursor=cursor,
        )
    except (SemanticRelayAuthorizationError, SemanticRelayServiceError) as exc:
        return _semantic_relay_error(exc)
    return jsonify({"ok": True, "acknowledged_cursor": acknowledged}), 200


@share_sessions_bp.route("/share-sessions/<session_id>", methods=["DELETE"])
@check_user_auth
def delete_share_session(session_id: str):
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if str(session_item.get("owner_user_id") or "") != user_id:
        return jsonify({"error": "forbidden"}), 403
    service.revoke_session(session_id=session_id, actor_user_id=user_id)
    get_share_relay_compatibility_service().clear_session(
        tenant_id=str(session_item.get("tenant_id") or "default"),
        session_id=session_id,
    )
    _view_started_audited.discard(session_id)
    _peer_key_repository.delete_scope(session_id)
    return jsonify({"ok": True}), 200


@share_sessions_bp.route("/share-sessions/<session_id>/heartbeat", methods=["POST"])
@check_user_auth
def share_session_heartbeat(session_id: str):
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict) or not _is_session_active(session_item):
        return jsonify({"error": "session_not_found"}), 404
    if str(session_item.get("owner_user_id") or "") == user_id:
        _participant_last_seen[f"owner-{user_id}"] = time.time()
    else:
        participants = service.get_participants(session_id)
        for p in participants:
            if str(p.get("user_id") or "") == user_id and not p.get("revoked_at"):
                _participant_last_seen[str(p.get("id") or "")] = time.time()
                break
    return jsonify({"ok": True}), 200


@share_sessions_bp.route("/share-sessions/<session_id>/join", methods=["POST"])
@check_user_auth
def join_share_session(session_id: str):
    auth = dict(get_request_auth_context() or {})
    user_id = str(auth.get("sub") or auth.get("username") or "").strip()
    if not str(auth.get("sub") or "").strip():
        return jsonify({"error": "oidc_context_required"}), 403
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    device_id = str(body.get("device_id") or _current_device_id() or "").strip()
    invite_code = str(body.get("invite_code") or "").strip()
    if not device_id:
        return jsonify({"error": "device_id_required"}), 400
    if not invite_code:
        return jsonify({"error": "invite_code_required"}), 400
    fingerprint = str(body.get("public_key_fingerprint") or "").strip()
    public_key_spki_b64 = str(body.get("public_key_spki_b64") or "").strip()
    service = get_share_session_service()
    joined = service.join_session(
        session_id=session_id,
        user_id=user_id,
        device_id=device_id,
        public_key_fingerprint=fingerprint,
        invite_code=invite_code,
        public_key_spki_b64=public_key_spki_b64,
        tenant_id=_current_tenant_id(),
    )
    if not joined.ok:
        if joined.reason in {"session_not_found"}:
            return jsonify({"error": joined.reason}), 404
        if joined.reason in {"invalid_invite", "session_revoked", "session_expired", "cross_tenant_denied"}:
            return jsonify({"error": joined.reason}), 403
        return jsonify({"error": joined.reason or "join_failed"}), 400
    participant = dict(joined.participant or {})
    audit_participant_joined(
        session_id=session_id,
        participant_id=str(participant.get("id") or ""),
        user_id=user_id,
        device_id=str(participant.get("device_id") or ""),
        public_key_fingerprint=str(participant.get("public_key_fingerprint") or ""),
        permissions=dict(participant.get("permissions") or {}),
    )
    return jsonify({"ok": True, "data": participant}), 201


@share_sessions_bp.route("/share-sessions/<session_id>/participants/join", methods=["POST"])
@check_user_auth
def join_share_session_participant(session_id: str):
    """Compatibility join endpoint for hub-relay clients that already know the session id."""
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_session_active(session_item):
        return jsonify({"error": "session_not_active"}), 403
    device_id = str(body.get("device_id") or _current_device_id() or f"web-{user_id[:16]}").strip()
    invite_code = str(body.get("invite_code") or session_item.get("invite_code") or "").strip()
    fingerprint = str(body.get("public_key_fingerprint") or "").strip()
    public_key_spki_b64 = str(body.get("public_key_spki_b64") or "").strip()
    joined = service.join_session(
        session_id=session_id,
        user_id=user_id,
        device_id=device_id,
        public_key_fingerprint=fingerprint,
        invite_code=invite_code,
        public_key_spki_b64=public_key_spki_b64,
        tenant_id=_current_tenant_id(),
    )
    if not joined.ok:
        if joined.reason == "session_not_found":
            return jsonify({"error": joined.reason}), 404
        if joined.reason in {"invalid_invite", "session_revoked", "session_expired", "cross_tenant_denied"}:
            return jsonify({"error": joined.reason}), 403
        return jsonify({"error": joined.reason or "join_failed"}), 400
    participant = dict(joined.participant or {})
    _participant_last_seen[str(participant.get("id") or "")] = time.time()
    audit_participant_joined(
        session_id=session_id,
        participant_id=str(participant.get("id") or ""),
        user_id=user_id,
        device_id=str(participant.get("device_id") or ""),
        public_key_fingerprint=str(participant.get("public_key_fingerprint") or ""),
        permissions=dict(participant.get("permissions") or {}),
    )
    return jsonify({"ok": True, "participant": participant, "data": participant}), 201


@share_sessions_bp.route("/share-sessions/<session_id>/permissions", methods=["PATCH"])
@check_user_auth
def patch_share_session_permissions(session_id: str):
    user_id = _current_user_id()
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    permissions = body.get("permissions")
    if not isinstance(permissions, dict):
        return jsonify({"error": "permissions_required"}), 400
    service = get_share_session_service()
    try:
        ok, reason, session_item = service.update_session_permissions(
            session_id=session_id,
            actor_user_id=user_id,
            permissions=permissions,
        )
    except PermissionContractError as exc:
        return jsonify({"error": exc.reason_code, "field": exc.field}), 400
    if not ok:
        if reason == "forbidden":
            return jsonify({"error": reason}), 403
        if reason == "session_not_found":
            return jsonify({"error": reason}), 404
        return jsonify({"error": reason or "update_failed"}), 400
    audit_permission_changed(
        session_id=session_id,
        actor_user_id=user_id,
        new_permissions=dict((session_item or {}).get("permissions") or {}),
    )
    return jsonify({"ok": True, "data": session_item}), 200


@share_sessions_bp.route("/share-sessions/<session_id>/view/push", methods=["POST"])
@check_user_auth
def push_view_payload(session_id: str):
    """Relay an opaque Pair payload; strict sessions expose no side metadata."""
    user_id = _current_user_id()
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_session_active(session_item):
        return jsonify({"error": "session_not_active"}), 403
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant"}), 403
    session_owner_user_id = str(session_item.get("owner_user_id") or "")
    if not _rate_limiter.allow_request(
        namespace=_VIEW_FRAME_RATE["namespace"],
        subject=f"{user_id}:{session_id}",
        limit=_VIEW_FRAME_RATE["limit"],
        window_seconds=_VIEW_FRAME_RATE["window_seconds"],
    ):
        return jsonify({"error": "rate_limited"}), 429
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    if not body.get("encrypted_payload"):
        return jsonify({"error": "encrypted_payload_required"}), 400
    raw = request.get_data(as_text=False)
    if len(raw) > _VIEW_PAYLOAD_MAX_BYTES:
        return jsonify({"error": "payload_too_large"}), 413
    if _strict_e2ee_enabled(session_item):
        if set(body) != {"message_id", "encrypted_payload"}:
            return jsonify({"error": "strict_envelope_fields_invalid"}), 400
        message_id = str(body.get("message_id") or "")
        if not message_id or len(message_id.encode("utf-8")) > 96:
            return jsonify({"error": "relay_item_id_invalid"}), 400
        current_epoch = get_webrtc_epoch_service().current_epoch("session", session_id)
        if current_epoch is None:
            return jsonify({"error": "security_epoch_unavailable"}), 409
        try:
            contract = _strict_security_contract(
                session_id=session_id,
                session_item=session_item,
                epoch=current_epoch,
            )
            secure = _share_envelope_security.validate(
                session_id=session_id,
                authenticated_sender_id=user_id,
                serialized=body.get("encrypted_payload"),
                allowed_payload_types=_STRICT_VIEW_TRAFFIC,
                traffic_by_payload=_STRICT_VIEW_TRAFFIC,
                expected_contract_digest=str(contract["digest"]),
                authorizer=_strict_pair_authorizer(
                    session_id=session_id,
                    session_item=session_item,
                    permission_by_payload=_STRICT_VIEW_PERMISSIONS,
                    contract_digest=str(contract["digest"]),
                ),
            )
        except (ShareViewSecurityError, ShareSecurityNegotiationError) as exc:
            return jsonify({"error": exc.reason_code}), exc.status_code
        try:
            get_share_relay_compatibility_service().publish_secure_envelope(
                tenant_id=str(session_item.get("tenant_id") or "default"),
                session_id=session_id,
                epoch=secure.epoch,
                sender_id=user_id,
                audience_id=secure.recipient.id,
                traffic_class="visual_semantic",
                item_id=message_id,
                item_id_field="message_id",
                serialized_envelope=str(body["encrypted_payload"]),
                queue_limit=_VIEW_QUEUE_MAX,
            )
        except ShareRelayCompatibilityError as exc:
            status = 413 if exc.reason_code in {"relay_envelope_too_large", "relay_item_invalid"} else 429
            return jsonify({"error": exc.reason_code}), status
        if secure.payload_type == "pair.view_delta" and session_id not in _view_started_audited:
            audit_view_started(session_id=session_id, owner_user_id=session_owner_user_id)
            _view_started_audited.add(session_id)
        audit_view_delta_sent(
            session_id=session_id,
            owner_user_id=session_owner_user_id,
            sender_user_id=user_id,
            kind=secure.payload_type,
            new_hash="",
            policy_hash=secure.aad.contract_digest,
        )
        return jsonify({"ok": True}), 200

    if session_item.get("owner_user_id") != user_id:
        return jsonify({"error": "forbidden"}), 403
    if not get_share_session_permission_service().allows(session_id, session_item.get("permissions"), "view_tui"):
        return jsonify({"error": "view_tui_permission_required"}), 403
    if session_id not in _view_started_audited:
        audit_view_started(session_id=session_id, owner_user_id=user_id)
        _view_started_audited.add(session_id)
    message_id = str(body.get("message_id") or str(uuid.uuid4()))
    entry: dict[str, Any] = {
        "session_id": session_id,
        "message_id": message_id,
        "kind": str(body.get("kind") or "snapshot"),
        "width": int(body.get("width") or 0),
        "height": int(body.get("height") or 0),
        "base_hash": str(body.get("base_hash") or ""),
        "new_hash": str(body.get("new_hash") or ""),
        "encrypted_payload": body.get("encrypted_payload"),
        "pushed_at": time.time(),
    }
    audience_ids = [
        str(participant.get("user_id") or "")
        for participant in service.get_participants(session_id)
        if participant.get("revoked_at") is None
    ]
    epoch = get_webrtc_epoch_service().current_epoch("session", session_id) or 1
    try:
        get_share_relay_compatibility_service().publish(
            tenant_id=str(session_item.get("tenant_id") or "default"),
            session_id=session_id,
            epoch=epoch,
            sender_id=user_id,
            audience_ids=audience_ids,
            traffic_class="visual_semantic",
            item=entry,
            item_id_field="message_id",
            queue_limit=_VIEW_QUEUE_MAX,
        )
    except ShareRelayCompatibilityError as exc:
        status = 413 if exc.reason_code in {"relay_envelope_too_large", "relay_item_invalid"} else 429
        return jsonify({"error": exc.reason_code}), status
    audit_view_delta_sent(
        session_id=session_id,
        owner_user_id=session_owner_user_id,
        sender_user_id=user_id,
        kind=str(entry["kind"]),
        new_hash=str(entry["new_hash"]),
        policy_hash=str(entry["base_hash"] or entry["new_hash"] or ""),
    )
    return jsonify({"ok": True}), 200


@share_sessions_bp.route("/share-sessions/<session_id>/view/poll", methods=["GET"])
@check_user_auth
def poll_view_payload(session_id: str):
    """SS05.04: Teilnehmer holt verschlüsselte Snapshots/Deltas ab."""
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_session_active(session_item):
        return jsonify({"error": "session_not_active"}), 403
    if not _rate_limiter.allow_request(
        namespace=_VIEW_POLL_RATE["namespace"],
        subject=f"{user_id}:{session_id}",
        limit=_VIEW_POLL_RATE["limit"],
        window_seconds=_VIEW_POLL_RATE["window_seconds"],
    ):
        return jsonify({"error": "rate_limited"}), 429
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant"}), 403
    if not _strict_e2ee_enabled(session_item) and not get_share_session_permission_service().allows(
        session_id, session_item.get("permissions"), "view_tui"
    ):
        return jsonify({"error": "view_tui_permission_required"}), 403
    since = str(request.args.get("since") or "").strip()
    frames, last_id = get_share_relay_compatibility_service().read(
        tenant_id=str(session_item.get("tenant_id") or "default"),
        session_id=session_id,
        audience_id=user_id,
        traffic_class="visual_semantic",
        since_item_id=since,
        item_id_field="message_id",
        queue_limit=_VIEW_QUEUE_MAX,
        page_limit=10,
    )
    if _strict_e2ee_enabled(session_item):
        view_messages = [
            {"message_id": frame.get("message_id"), "encrypted_payload": frame.get("encrypted_payload")}
            for frame in frames
        ]
        return jsonify({"ok": True, "view_messages": view_messages, "view_cursor": last_id or ""}), 200

    # T06: Pair-Dev view-sync contract. The frontend expects
    # `view_messages` (a flat list of RelayEnvelopes) and a
    # `view_cursor` to advance through the queue. We keep the
    # legacy `data.frames` shape for backwards compatibility
    # with any older client still on it.
    view_messages = [
        {
            "message_id": f.get("message_id"),
            "kind": f.get("kind"),
            "base_hash": f.get("base_hash"),
            "new_hash": f.get("new_hash"),
            "width": f.get("width"),
            "height": f.get("height"),
            "encrypted_payload": f.get("encrypted_payload"),
        }
        for f in frames
    ]
    return jsonify(
        {
            "ok": True,
            "view_messages": view_messages,
            "messages": view_messages,
            "payloads": view_messages,
            "view_cursor": last_id or "",
            "data": {"frames": frames},
        }
    ), 200


@share_sessions_bp.route("/share-sessions/<session_id>/chat/messages", methods=["POST"])
@check_user_auth
def send_share_chat_message(session_id: str):
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_session_active(session_item):
        return jsonify({"error": "session_not_active", "blocked": True}), 403
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant", "blocked": True}), 403
    if not get_share_session_permission_service().allows(session_id, session_item.get("permissions"), "chat"):
        return jsonify({"error": "chat_permission_required", "blocked": True}), 403
    if not _rate_limiter.allow_request(
        namespace=_CHAT_SEND_RATE["namespace"],
        subject=f"{user_id}:{session_id}",
        limit=_CHAT_SEND_RATE["limit"],
        window_seconds=_CHAT_SEND_RATE["window_seconds"],
    ):
        return jsonify({"error": "rate_limited", "blocked": True}), 429

    raw = request.get_data(as_text=False)
    if len(raw) > _CHAT_MSG_MAX_BYTES:
        return jsonify({"error": "payload_too_large", "blocked": True}), 413
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    if _strict_e2ee_enabled(session_item):
        if set(body) != {"id", "encrypted_payload"}:
            return jsonify({"error": "strict_envelope_fields_invalid", "blocked": True}), 400
        message_id = str(body.get("id") or "")
        if not message_id or len(message_id.encode("utf-8")) > 96:
            return jsonify({"error": "relay_item_id_invalid", "blocked": True}), 400
        current_epoch = get_webrtc_epoch_service().current_epoch("session", session_id)
        if current_epoch is None:
            return jsonify({"error": "security_epoch_unavailable", "blocked": True}), 409
        try:
            contract = _strict_security_contract(
                session_id=session_id,
                session_item=session_item,
                epoch=current_epoch,
            )
            secure = _share_envelope_security.validate(
                session_id=session_id,
                authenticated_sender_id=user_id,
                serialized=body.get("encrypted_payload"),
                allowed_payload_types={"pair.chat_message"},
                traffic_by_payload={"pair.chat_message": "semantic"},
                expected_contract_digest=str(contract["digest"]),
                authorizer=_strict_pair_authorizer(
                    session_id=session_id,
                    session_item=session_item,
                    permission_by_payload={"pair.chat_message": "chat"},
                    contract_digest=str(contract["digest"]),
                ),
            )
            get_share_relay_compatibility_service().publish_secure_envelope(
                tenant_id=str(session_item.get("tenant_id") or "default"),
                session_id=session_id,
                epoch=secure.epoch,
                sender_id=user_id,
                audience_id=secure.recipient.id,
                traffic_class="transcript",
                item_id=message_id,
                item_id_field="id",
                serialized_envelope=str(body["encrypted_payload"]),
                queue_limit=_CHAT_QUEUE_MAX,
            )
        except (ShareViewSecurityError, ShareSecurityNegotiationError) as exc:
            return jsonify({"error": exc.reason_code, "blocked": True}), exc.status_code
        except ShareRelayCompatibilityError as exc:
            status = 413 if exc.reason_code in {"relay_envelope_too_large", "relay_item_invalid"} else 429
            return jsonify({"error": exc.reason_code, "blocked": True}), status
        audit_chat_sent(
            session_id=session_id,
            sender_user_id=user_id,
            message_id=message_id,
            is_encrypted=True,
        )
        return jsonify({"ok": True, "data": {"id": message_id}, "blocked": False}), 201

    message_id = str(body.get("id") or str(uuid.uuid4()))
    encrypted_payload = body.get("encrypted_payload")
    text = str(body.get("text") or "")
    if not encrypted_payload and not text:
        return jsonify({"error": "message_required", "blocked": True}), 400
    message = {
        "id": message_id,
        "share_session_id": session_id,
        "from_id": str(body.get("from_id") or user_id),
        "channel_type": str(body.get("channel_type") or "room"),
        "visibility": str(body.get("visibility") or "room"),
        "encrypted_payload": encrypted_payload,
        "text": text,
        "created_at": time.time(),
    }
    audience_ids = {
        str(session_item.get("owner_user_id") or ""),
        user_id,
        *(
            str(participant.get("user_id") or "")
            for participant in service.get_participants(session_id)
            if participant.get("revoked_at") is None
        ),
    }
    epoch = get_webrtc_epoch_service().current_epoch("session", session_id) or 1
    try:
        get_share_relay_compatibility_service().publish(
            tenant_id=str(session_item.get("tenant_id") or "default"),
            session_id=session_id,
            epoch=epoch,
            sender_id=user_id,
            audience_ids=list(audience_ids),
            traffic_class="transcript",
            item=message,
            item_id_field="id",
            queue_limit=_CHAT_QUEUE_MAX,
        )
    except ShareRelayCompatibilityError as exc:
        status = 413 if exc.reason_code in {"relay_envelope_too_large", "relay_item_invalid"} else 429
        return jsonify({"error": exc.reason_code, "blocked": True}), status
    audit_chat_sent(
        session_id=session_id,
        sender_user_id=user_id,
        message_id=message_id,
        is_encrypted=bool(encrypted_payload),
    )
    return jsonify({"ok": True, "data": {"id": message_id}, "blocked": False}), 201


@share_sessions_bp.route("/share-sessions/<session_id>/chat/messages", methods=["GET"])
@check_user_auth
def list_share_chat_messages(session_id: str):
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_session_active(session_item):
        return jsonify({"error": "session_not_active"}), 403
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant"}), 403
    if not get_share_session_permission_service().allows(session_id, session_item.get("permissions"), "chat"):
        return jsonify({"error": "chat_permission_required"}), 403
    if not _rate_limiter.allow_request(
        namespace=_CHAT_POLL_RATE["namespace"],
        subject=f"{user_id}:{session_id}",
        limit=_CHAT_POLL_RATE["limit"],
        window_seconds=_CHAT_POLL_RATE["window_seconds"],
    ):
        return jsonify({"error": "rate_limited"}), 429

    since = str(request.args.get("since") or "").strip()
    messages, cursor = get_share_relay_compatibility_service().read(
        tenant_id=str(session_item.get("tenant_id") or "default"),
        session_id=session_id,
        audience_id=user_id,
        traffic_class="transcript",
        since_item_id=since,
        item_id_field="id",
        queue_limit=_CHAT_QUEUE_MAX,
        page_limit=100,
    )
    return jsonify({"ok": True, "messages": messages, "cursor": cursor}), 200


@share_sessions_bp.route("/share-sessions/<session_id>/participants/<participant_id>", methods=["DELETE"])
@check_user_auth
def revoke_share_session_participant(session_id: str, participant_id: str):
    user_id = _current_user_id()
    service = get_share_session_service()
    ok, reason, participant = service.revoke_participant(
        session_id=session_id,
        participant_id=participant_id,
        actor_user_id=user_id,
    )
    if not ok:
        if reason == "forbidden":
            return jsonify({"error": reason}), 403
        if reason in {"session_not_found", "participant_not_found"}:
            return jsonify({"error": reason}), 404
        return jsonify({"error": reason or "revoke_failed"}), 400
    audit_participant_revoked(session_id=session_id, participant_id=participant_id, actor_user_id=user_id)
    return jsonify({"ok": True, "data": participant}), 200
