"""SS07.01: Audit-Events für Shared Sessions.

Events: session_created, participant_joined, participant_revoked,
        permission_changed, chat_sent, view_started, view_delta_sent, view_stopped.
Enthält keine Chat- oder View-Klartextinhalte.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from agent.common.audit import log_audit


_EVENT_TYPES = {
    "session_created",
    "participant_joined",
    "participant_revoked",
    "permission_changed",
    "chat_sent",
    "view_started",
    "view_delta_sent",
    "view_stopped",
    "session_revoked",
}


def _sub_hash(sub: str) -> str:
    return hashlib.sha256(str(sub).encode()).hexdigest()[:16]


def audit_session_created(
    *,
    session_id: str,
    owner_user_id: str,
    owner_device_id: str,
    mode: str,
    transport: str,
    permissions: dict[str, Any],
) -> None:
    log_audit("share.session_created", {
        "session_id": session_id,
        "owner_user_hash": _sub_hash(owner_user_id),
        "owner_device_id": owner_device_id,
        "mode": mode,
        "transport": transport,
        "permissions": {k: bool(v) for k, v in permissions.items()},
    })


def audit_participant_joined(
    *,
    session_id: str,
    participant_id: str,
    user_id: str,
    device_id: str,
    public_key_fingerprint: str,
    permissions: dict[str, Any],
) -> None:
    log_audit("share.participant_joined", {
        "session_id": session_id,
        "participant_id": participant_id,
        "user_hash": _sub_hash(user_id),
        "device_id": device_id,
        "fingerprint": public_key_fingerprint,
        "permissions": {k: bool(v) for k, v in permissions.items()},
    })


def audit_participant_revoked(
    *,
    session_id: str,
    participant_id: str,
    actor_user_id: str,
) -> None:
    log_audit("share.participant_revoked", {
        "session_id": session_id,
        "participant_id": participant_id,
        "actor_user_hash": _sub_hash(actor_user_id),
        "revoked_at": time.time(),
    })


def audit_permission_changed(
    *,
    session_id: str,
    actor_user_id: str,
    new_permissions: dict[str, Any],
) -> None:
    log_audit("share.permission_changed", {
        "session_id": session_id,
        "actor_user_hash": _sub_hash(actor_user_id),
        "new_permissions": {k: bool(v) for k, v in new_permissions.items()},
    })


def audit_chat_sent(
    *,
    session_id: str,
    sender_user_id: str,
    message_id: str,
    is_encrypted: bool,
) -> None:
    """Kein Klartext-Inhalt im Audit."""
    log_audit("share.chat_sent", {
        "session_id": session_id,
        "sender_user_hash": _sub_hash(sender_user_id),
        "message_id": message_id,
        "is_encrypted": is_encrypted,
    })


def audit_view_started(
    *,
    session_id: str,
    owner_user_id: str,
) -> None:
    log_audit("share.view_started", {
        "session_id": session_id,
        "owner_user_hash": _sub_hash(owner_user_id),
        "started_at": time.time(),
    })


def audit_view_delta_sent(
    *,
    session_id: str,
    owner_user_id: str,
    kind: str,
    new_hash: str,
    policy_hash: str,
) -> None:
    """Nur Hash-Metadaten – kein Screenshot-Klartext."""
    log_audit("share.view_delta_sent", {
        "session_id": session_id,
        "owner_user_hash": _sub_hash(owner_user_id),
        "kind": kind,
        "new_hash": new_hash,
        "policy_hash": policy_hash,
    })


def audit_view_stopped(
    *,
    session_id: str,
    owner_user_id: str,
    reason: str = "",
) -> None:
    log_audit("share.view_stopped", {
        "session_id": session_id,
        "owner_user_hash": _sub_hash(owner_user_id),
        "reason": reason,
        "stopped_at": time.time(),
    })


def audit_session_revoked(
    *,
    session_id: str,
    actor_user_id: str,
) -> None:
    log_audit("share.session_revoked", {
        "session_id": session_id,
        "actor_user_hash": _sub_hash(actor_user_id),
        "revoked_at": time.time(),
    })
