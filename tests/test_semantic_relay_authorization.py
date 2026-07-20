from __future__ import annotations

import pytest

from agent.services.semantic_relay_authorization import (
    RelayMember,
    SemanticRelayAuthorization,
    SemanticRelayAuthorizationError,
)


class Memberships:
    def __init__(self, *members: RelayMember) -> None:
        self.rows = {(row.tenant_id, row.session_id, row.member_id): row for row in members}

    def member(self, *, tenant_id: str, session_id: str, member_id: str):
        return self.rows.get((tenant_id, session_id, member_id))


def _member(member_id: str, *, tenant: str = "tenant-a", epoch: int = 2, active: bool = True) -> RelayMember:
    return RelayMember(
        tenant_id=tenant,
        session_id="session-a",
        member_id=member_id,
        epoch=epoch,
        active=active,
        permissions=frozenset({"semantic_control"}),
        send_audiences=frozenset({"peer-b"}) if member_id == "peer-a" else frozenset({"peer-a"}),
    )


def test_bilateral_authorization_requires_current_sender_audience_permission_and_epoch() -> None:
    authorization = SemanticRelayAuthorization(Memberships(_member("peer-a"), _member("peer-b")))
    authorization.require_send(
        tenant_id="tenant-a",
        session_id="session-a",
        sender_id="peer-a",
        audience_id="peer-b",
        epoch=2,
        required_permission="semantic_control",
    )
    for kwargs, reason in (
        ({"epoch": 1}, "relay_epoch_stale"),
        ({"audience_id": "unknown"}, "relay_membership_required"),
        ({"required_permission": "peer_evidence_sync"}, "relay_permission_required"),
    ):
        values = {
            "tenant_id": "tenant-a",
            "session_id": "session-a",
            "sender_id": "peer-a",
            "audience_id": "peer-b",
            "epoch": 2,
            "required_permission": "semantic_control",
            **kwargs,
        }
        with pytest.raises(SemanticRelayAuthorizationError, match=reason):
            authorization.require_send(**values)


def test_owner_label_cannot_impersonate_sender_or_read_private_audience() -> None:
    authorization = SemanticRelayAuthorization(Memberships(_member("owner"), _member("peer-b")))
    with pytest.raises(SemanticRelayAuthorizationError, match="relay_audience_denied"):
        authorization.require_send(
            tenant_id="tenant-a",
            session_id="session-a",
            sender_id="owner",
            audience_id="peer-b",
            epoch=2,
            required_permission="semantic_control",
        )
    with pytest.raises(SemanticRelayAuthorizationError, match="relay_membership_required"):
        authorization.require_read(
            tenant_id="tenant-a",
            session_id="session-a",
            audience_id="peer-a",
            epoch=2,
            required_permission="semantic_control",
        )
