"""SSH access audit service — correlates SSH certificate issuance with terminal session lifecycle.

Never stores OIDC tokens, private certificate material, raw bearer tokens, or passwords.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from agent.common.audit import log_audit
from agent.config import settings

LOGGER = logging.getLogger("agent.ssh_access_audit")

# Audit event name constants
EVT_SSH_CERT_ISSUED = "ssh_certificate_issued"
EVT_SSH_CERT_DENIED = "ssh_certificate_issuance_denied"
EVT_SSH_CERT_EXPIRED = "ssh_certificate_expired"
EVT_SSH_SESSION_CREATED = "ssh_terminal_session_created"
EVT_SSH_SESSION_ATTACHED = "ssh_terminal_session_attached"
EVT_SSH_SESSION_DETACHED = "ssh_terminal_session_detached"
EVT_SSH_SESSION_WRITE = "ssh_terminal_session_write"
EVT_SSH_SESSION_KILLED = "ssh_terminal_session_killed"
EVT_SSH_POLICY_DENIED = "ssh_terminal_policy_denied"


@dataclass(frozen=True)
class SshAuditRecord:
    event: str
    user_id: str
    key_id: str | None
    principal: str | None
    target_type: str
    target_id: str | None
    session_id: str | None
    decision_id: str | None
    policy_version: str | None
    auth_source: str
    ts: float
    extra: dict


class SshAccessAuditService:
    """Audit layer for SSH certificate issuance and SSH terminal lifecycle events."""

    def record_certificate_issued(
        self,
        *,
        user_id: str,
        key_id: str,
        principal: str,
        target_type: str,
        decision_id: str,
        expires_at: float,
        policy_version: str,
    ) -> None:
        if not settings.ssh_audit_enabled:
            return
        record = SshAuditRecord(
            event=EVT_SSH_CERT_ISSUED,
            user_id=user_id[:40],
            key_id=key_id,
            principal=principal,
            target_type=target_type,
            target_id=None,
            session_id=None,
            decision_id=decision_id,
            policy_version=policy_version,
            auth_source="oidc",
            ts=time.time(),
            extra={"expires_at": expires_at},
        )
        self._emit(record)

    def record_certificate_denied(
        self,
        *,
        user_id: str,
        reason_code: str,
        target_type: str,
        decision_id: str,
    ) -> None:
        if not settings.ssh_audit_enabled:
            return
        record = SshAuditRecord(
            event=EVT_SSH_CERT_DENIED,
            user_id=user_id[:40],
            key_id=None,
            principal=None,
            target_type=target_type,
            target_id=None,
            session_id=None,
            decision_id=decision_id,
            policy_version=settings.terminal_policy_version,
            auth_source="oidc",
            ts=time.time(),
            extra={"reason_code": reason_code},
        )
        self._emit(record)

    def record_session_created(
        self,
        *,
        user_id: str,
        key_id: str | None,
        principal: str | None,
        target_type: str,
        target_id: str,
        session_id: str,
        decision_id: str | None,
    ) -> None:
        if not settings.ssh_audit_enabled:
            return
        record = SshAuditRecord(
            event=EVT_SSH_SESSION_CREATED,
            user_id=user_id[:40],
            key_id=key_id,
            principal=principal,
            target_type=target_type,
            target_id=target_id[:40],
            session_id=session_id,
            decision_id=decision_id,
            policy_version=settings.terminal_policy_version,
            auth_source="ssh_certificate",
            ts=time.time(),
            extra={},
        )
        self._emit(record)

    def record_session_attached(
        self,
        *,
        user_id: str,
        principal: str | None,
        session_id: str,
        target_type: str,
    ) -> None:
        if not settings.ssh_audit_enabled:
            return
        record = SshAuditRecord(
            event=EVT_SSH_SESSION_ATTACHED,
            user_id=user_id[:40],
            key_id=None,
            principal=principal,
            target_type=target_type,
            target_id=None,
            session_id=session_id,
            decision_id=None,
            policy_version=None,
            auth_source="ssh_certificate",
            ts=time.time(),
            extra={},
        )
        self._emit(record)

    def record_session_detached(self, *, user_id: str, session_id: str) -> None:
        if not settings.ssh_audit_enabled:
            return
        record = SshAuditRecord(
            event=EVT_SSH_SESSION_DETACHED,
            user_id=user_id[:40],
            key_id=None,
            principal=None,
            target_type="unknown",
            target_id=None,
            session_id=session_id,
            decision_id=None,
            policy_version=None,
            auth_source="ssh_certificate",
            ts=time.time(),
            extra={},
        )
        self._emit(record)

    def record_policy_denied(
        self,
        *,
        user_id: str,
        reason_code: str,
        operation: str,
        target_type: str,
        target_id: str,
    ) -> None:
        if not settings.ssh_audit_enabled:
            return
        record = SshAuditRecord(
            event=EVT_SSH_POLICY_DENIED,
            user_id=user_id[:40],
            key_id=None,
            principal=None,
            target_type=target_type,
            target_id=target_id[:40],
            session_id=None,
            decision_id=None,
            policy_version=settings.terminal_policy_version,
            auth_source="ssh_certificate",
            ts=time.time(),
            extra={"reason_code": reason_code, "operation": operation},
        )
        self._emit(record)

    @staticmethod
    def _emit(record: SshAuditRecord) -> None:
        try:
            details: dict[str, Any] = {
                "event": record.event,
                "user_id": record.user_id,
                "target_type": record.target_type,
                "auth_source": record.auth_source,
                "ts": record.ts,
            }
            if record.key_id:
                details["key_id"] = record.key_id
            if record.principal:
                details["principal"] = record.principal
            if record.target_id:
                details["target_id"] = record.target_id
            if record.session_id:
                details["session_id"] = record.session_id
            if record.decision_id:
                details["decision_id"] = record.decision_id
            if record.policy_version:
                details["policy_version"] = record.policy_version
            details.update(record.extra)
            log_audit(record.event, details)
        except Exception as exc:
            LOGGER.warning("SSH audit emit failed: %s", exc)


_INSTANCE: SshAccessAuditService | None = None


def get_ssh_access_audit_service() -> SshAccessAuditService:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = SshAccessAuditService()
    return _INSTANCE
