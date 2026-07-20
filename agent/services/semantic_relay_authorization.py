"""Bilateral, epoch-bound authorization for opaque relay delivery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class SemanticRelayAuthorizationError(PermissionError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class RelayMember:
    tenant_id: str
    session_id: str
    member_id: str
    epoch: int
    active: bool
    permissions: frozenset[str]
    send_audiences: frozenset[str]


class RelayMembershipPort(Protocol):
    def member(self, *, tenant_id: str, session_id: str, member_id: str) -> RelayMember | None: ...


class SemanticRelayAuthorization:
    def __init__(self, membership: RelayMembershipPort) -> None:
        self._membership = membership

    def require_send(
        self,
        *,
        tenant_id: str,
        session_id: str,
        sender_id: str,
        audience_id: str,
        epoch: int,
        required_permission: str,
    ) -> None:
        sender = self._membership.member(tenant_id=tenant_id, session_id=session_id, member_id=sender_id)
        audience = self._membership.member(tenant_id=tenant_id, session_id=session_id, member_id=audience_id)
        if sender is None or audience is None:
            raise SemanticRelayAuthorizationError("relay_membership_required")
        if sender.tenant_id != tenant_id or audience.tenant_id != tenant_id:
            raise SemanticRelayAuthorizationError("relay_tenant_mismatch")
        if not sender.active or not audience.active:
            raise SemanticRelayAuthorizationError("relay_membership_revoked")
        if sender.epoch != epoch or audience.epoch != epoch:
            raise SemanticRelayAuthorizationError("relay_epoch_stale")
        if required_permission not in sender.permissions or required_permission not in audience.permissions:
            raise SemanticRelayAuthorizationError("relay_permission_required")
        if audience_id not in sender.send_audiences:
            raise SemanticRelayAuthorizationError("relay_audience_denied")

    def require_read(
        self, *, tenant_id: str, session_id: str, audience_id: str, epoch: int, required_permission: str
    ) -> None:
        member = self._membership.member(tenant_id=tenant_id, session_id=session_id, member_id=audience_id)
        if member is None or member.tenant_id != tenant_id:
            raise SemanticRelayAuthorizationError("relay_membership_required")
        if not member.active:
            raise SemanticRelayAuthorizationError("relay_membership_revoked")
        if member.epoch != epoch:
            raise SemanticRelayAuthorizationError("relay_epoch_stale")
        if required_permission not in member.permissions:
            raise SemanticRelayAuthorizationError("relay_permission_required")


__all__ = [
    "RelayMember",
    "RelayMembershipPort",
    "SemanticRelayAuthorization",
    "SemanticRelayAuthorizationError",
]
