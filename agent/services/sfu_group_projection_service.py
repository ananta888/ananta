"""Hub application service for fenced, atomic receiver-group projection."""

from __future__ import annotations

from agent.services.sfu_broadcast_control_observability import (
    SfuBroadcastControlObservationPort,
    control_observer_or_null,
    observed_control_path,
)

import hashlib
import json
from dataclasses import asdict, dataclass

from agent.services.sfu_broadcast_repository_ports import (
    SfuAtomicGroupProjectionMutation,
    SfuAtomicGroupProjectionRepositoryPort,
    SfuProjectionMutation,
    SfuProjectionMutationResult,
    SfuReceiverGroup,
)
from agent.services.sfu_receiver_group_projector import ProjectedReceiverGroup


@dataclass(frozen=True, slots=True)
class SfuGroupProjectionCommand:
    projection_id: str
    audience_projection_id: str
    tenant_id: str
    session_id: str
    room_state_id: str
    room_state_revision: int
    group: ProjectedReceiverGroup
    policy_epoch: int
    expected_version: int
    fencing_token: int
    ttl_seconds: int
    retention_seconds: int
    idempotency_key: str
    audit_actor_ref: str
    audit_reason: str
    now: float


class SfuGroupProjectionService:
    """Maps pure projector output to one atomic repository command."""

    def __init__(
        self, *, repository: SfuAtomicGroupProjectionRepositoryPort,
        control_observer: SfuBroadcastControlObservationPort | None = None,
    ) -> None:
        self._repository = repository
        self._control_observer = control_observer_or_null(control_observer)

    @observed_control_path("group_projection")
    def project(
        self, command: SfuGroupProjectionCommand
    ) -> SfuProjectionMutationResult[SfuReceiverGroup]:
        expires_at = command.now + command.ttl_seconds
        request_digest = _canonical_digest(
            {
                "domain": "ananta:sfu-group-projection-command:v1",
                "command": asdict(command),
            }
        )
        value = SfuReceiverGroup(
            id=command.projection_id,
            tenant_id=command.tenant_id,
            session_id=command.session_id,
            room_state_id=command.room_state_id,
            room_state_revision=command.room_state_revision,
            status="active",
            ttl_seconds=command.ttl_seconds,
            retention_seconds=command.retention_seconds,
            retention_status="live",
            expires_at=expires_at,
            retain_until=expires_at + command.retention_seconds,
            tombstoned_at=None,
            tombstone_reason=None,
            fencing_token=command.fencing_token,
            version=max(1, command.expected_version + 1),
            audit_actor_ref=command.audit_actor_ref,
            audit_reason=command.audit_reason,
            request_digest=request_digest,
            idempotency_key_digest="0" * 64,
            created_at=command.now,
            updated_at=command.now,
            audited_at=command.now,
            receiver_group_ref=command.group.group_ref,
            subscription_ref=command.group.subscription_ref,
            group_digest=command.group.group_digest,
            membership_digest=command.group.membership_digest,
            key_digest=command.group.key_digest,
            membership_epoch=command.group.membership_epoch,
            key_epoch=command.group.key_epoch,
            topology_epoch=command.group.topology_epoch,
        )
        return self._repository.save_authorized(
            SfuAtomicGroupProjectionMutation(
                audience_projection_id=command.audience_projection_id,
                mutation=SfuProjectionMutation(
                    value=value,
                    expected_version=command.expected_version,
                    idempotency_key=command.idempotency_key,
                ),
                expected_policy_epoch=command.policy_epoch,
                expected_membership_epoch=command.group.membership_epoch,
                expected_key_epoch=command.group.key_epoch,
            ),
            now=command.now,
        )


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["SfuGroupProjectionCommand", "SfuGroupProjectionService"]
