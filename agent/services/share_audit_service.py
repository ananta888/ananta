"""SS07.01: Audit-Events für Shared Sessions.

Events: session_created, participant_joined, participant_revoked,
        permission_changed, chat_sent, view_started, view_delta_sent, view_stopped.
Enthält keine Chat- oder View-Klartextinhalte.
"""
from __future__ import annotations

import hashlib
import json
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


def _digest(kind: str, value: str) -> str:
    return hashlib.sha256(f"ananta.share.audit.v2\0{kind}\0{value}".encode()).hexdigest()


def _permission_evidence(permissions: dict[str, Any]) -> dict[str, Any]:
    normalized = {str(key): bool(value) for key, value in permissions.items()}
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return {
        "permission_policy_digest": _digest("permission-policy", canonical),
        "granted_permission_count": sum(normalized.values()),
        "permission_count": len(normalized),
    }


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
        "scope_digest": _digest("session", session_id),
        "owner_digest": _digest("user", owner_user_id),
        "device_digest": _digest("device", owner_device_id),
        "mode": mode,
        "transport": transport,
        **_permission_evidence(permissions),
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
        "scope_digest": _digest("session", session_id),
        "participant_digest": _digest("participant", participant_id),
        "user_digest": _digest("user", user_id),
        "device_digest": _digest("device", device_id),
        "public_key_fingerprint_digest": _digest("fingerprint", public_key_fingerprint),
        **_permission_evidence(permissions),
    })


def audit_participant_revoked(
    *,
    session_id: str,
    participant_id: str,
    actor_user_id: str,
) -> None:
    log_audit("share.participant_revoked", {
        "scope_digest": _digest("session", session_id),
        "participant_digest": _digest("participant", participant_id),
        "actor_digest": _digest("user", actor_user_id),
        "revoked_at": time.time(),
    })


def audit_permission_changed(
    *,
    session_id: str,
    actor_user_id: str,
    new_permissions: dict[str, Any],
) -> None:
    log_audit("share.permission_changed", {
        "scope_digest": _digest("session", session_id),
        "actor_digest": _digest("user", actor_user_id),
        **_permission_evidence(new_permissions),
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
        "scope_digest": _digest("session", session_id),
        "sender_digest": _digest("user", sender_user_id),
        "message_digest": _digest("message", message_id),
        "is_encrypted": is_encrypted,
    })


def audit_view_started(
    *,
    session_id: str,
    owner_user_id: str,
) -> None:
    log_audit("share.view_started", {
        "scope_digest": _digest("session", session_id),
        "owner_digest": _digest("user", owner_user_id),
        "started_at": time.time(),
    })


def audit_view_delta_sent(
    *,
    session_id: str,
    owner_user_id: str,
    sender_user_id: str | None = None,
    kind: str,
    new_hash: str,
    policy_hash: str,
) -> None:
    """Nur Hash-Metadaten und getrennte Rollen-Digests – kein View-Klartext."""
    effective_sender_user_id = sender_user_id or owner_user_id
    log_audit("share.view_delta_sent", {
        "scope_digest": _digest("session", session_id),
        "owner_digest": _digest("user", owner_user_id),
        "sender_digest": _digest("user", effective_sender_user_id),
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
        "scope_digest": _digest("session", session_id),
        "owner_digest": _digest("user", owner_user_id),
        "reason": reason,
        "stopped_at": time.time(),
    })


def audit_session_revoked(
    *,
    session_id: str,
    actor_user_id: str,
) -> None:
    log_audit("share.session_revoked", {
        "scope_digest": _digest("session", session_id),
        "actor_digest": _digest("user", actor_user_id),
        "revoked_at": time.time(),
    })
