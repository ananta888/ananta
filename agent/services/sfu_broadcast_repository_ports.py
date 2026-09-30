"""Compatibility re-export of the SFU broadcast projection persistence contract.

New code imports the projection value types from
:mod:`agent.models.sfu_broadcast_projection` and the persistence protocols from
:mod:`agent.ports.sfu_broadcast_projection`.
"""

from agent.models.sfu_broadcast_projection import (
    SfuAtomicGroupProjectionMutation,
    SfuAudienceRetentionFence,
    SfuAudienceRetentionPurgePage,
    SfuBroadcastAudience,
    SfuBroadcastRoomScope,
    SfuFanoutRoute,
    SfuMutationStatus,
    SfuProjectionEnvelope,
    SfuProjectionMutation,
    SfuProjectionMutationResult,
    SfuProjectionPage,
    SfuProjectionStatus,
    SfuReceiverGroup,
    SfuRetentionStatus,
)
from agent.ports.sfu_broadcast_projection import (
    SfuAtomicGroupProjectionRepositoryPort,
    SfuAudienceSnapshotRetentionRepositoryPort,
    SfuBroadcastAudienceRepositoryPort,
    SfuFanoutRouteRepositoryPort,
    SfuReceiverGroupRepositoryPort,
)

__all__ = [
    "SfuAtomicGroupProjectionMutation",
    "SfuAtomicGroupProjectionRepositoryPort",
    "SfuAudienceRetentionFence",
    "SfuAudienceRetentionPurgePage",
    "SfuAudienceSnapshotRetentionRepositoryPort",
    "SfuBroadcastAudience",
    "SfuBroadcastAudienceRepositoryPort",
    "SfuBroadcastRoomScope",
    "SfuFanoutRoute",
    "SfuFanoutRouteRepositoryPort",
    "SfuMutationStatus",
    "SfuProjectionEnvelope",
    "SfuProjectionMutation",
    "SfuProjectionMutationResult",
    "SfuProjectionPage",
    "SfuProjectionStatus",
    "SfuReceiverGroup",
    "SfuReceiverGroupRepositoryPort",
    "SfuRetentionStatus",
]
