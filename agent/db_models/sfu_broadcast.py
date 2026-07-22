from __future__ import annotations

import time
import uuid

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


_ACTIVE_PROJECTION = sa.text("status = 'active' AND tombstoned_at IS NULL")


def _projection_constraints(prefix: str) -> tuple[sa.CheckConstraint, ...]:
    return (
        sa.CheckConstraint(
            "room_state_revision > 0",
            name=f"ck_{prefix}_room_revision_positive",
        ),
        sa.CheckConstraint(
            "fencing_token >= 0",
            name=f"ck_{prefix}_fence_non_negative",
        ),
        sa.CheckConstraint("version > 0", name=f"ck_{prefix}_version_positive"),
        sa.CheckConstraint("ttl_seconds > 0", name=f"ck_{prefix}_ttl_positive"),
        sa.CheckConstraint(
            "retention_seconds >= 0",
            name=f"ck_{prefix}_retention_non_negative",
        ),
        sa.CheckConstraint(
            "expires_at > created_at AND retain_until >= expires_at",
            name=f"ck_{prefix}_lifecycle_order",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at AND audited_at >= created_at",
            name=f"ck_{prefix}_audit_order",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'draining', 'expired', 'revoked', 'tombstoned')",
            name=f"ck_{prefix}_status",
        ),
        sa.CheckConstraint(
            "retention_status IN ('live', 'retained', 'purge_pending', 'purged')",
            name=f"ck_{prefix}_retention_status",
        ),
        sa.CheckConstraint(
            "((status = 'tombstoned' AND tombstoned_at IS NOT NULL) OR "
            "(status <> 'tombstoned' AND tombstoned_at IS NULL))",
            name=f"ck_{prefix}_tombstone_state",
        ),
        sa.CheckConstraint(
            "((tombstoned_at IS NULL AND tombstone_reason IS NULL) OR tombstoned_at IS NOT NULL)",
            name=f"ck_{prefix}_tombstone_reason",
        ),
        sa.CheckConstraint(
            "length(request_digest) = 64 AND length(idempotency_key_digest) = 64",
            name=f"ck_{prefix}_audit_digest_length",
        ),
    )


class _SfuBroadcastProjectionDB(SQLModel):
    """Lifecycle and CAS envelope shared by Hub-owned broadcast projections."""

    tenant_id: str
    session_id: str
    room_state_id: str
    room_state_revision: int
    status: str = "pending"
    ttl_seconds: int
    retention_seconds: int
    retention_status: str = "live"
    expires_at: float
    retain_until: float
    tombstoned_at: float | None = None
    tombstone_reason: str | None = None
    fencing_token: int = 0
    version: int = 1
    audit_actor_ref: str
    audit_reason: str
    request_digest: str = Field(repr=False)
    idempotency_key_digest: str = Field(repr=False)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    audited_at: float = Field(default_factory=time.time)


class SfuBroadcastAudienceDB(_SfuBroadcastProjectionDB, table=True):
    """Digest-only projection of one canonical publication audience."""

    __tablename__ = "sfu_broadcast_audiences"
    __table_args__ = _projection_constraints("sfu_broadcast_audience") + (
        sa.CheckConstraint(
            "policy_epoch >= 0 AND membership_epoch >= 0 AND key_epoch >= 0",
            name="ck_sfu_broadcast_audience_epochs_non_negative",
        ),
        sa.CheckConstraint(
            "length(audience_digest) = 64 AND length(policy_digest) = 64 "
            "AND length(membership_digest) = 64",
            name="ck_sfu_broadcast_audience_digest_length",
        ),
        sa.Index(
            "uq_sfu_broadcast_audience_active_publication",
            "tenant_id",
            "session_id",
            "publication_ref",
            unique=True,
            sqlite_where=_ACTIVE_PROJECTION,
            postgresql_where=_ACTIVE_PROJECTION,
        ),
        sa.Index(
            "ix_sfu_broadcast_audience_room_revision",
            "tenant_id",
            "session_id",
            "room_state_revision",
        ),
        sa.Index(
            "ix_sfu_broadcast_audience_retention",
            "status",
            "expires_at",
            "retain_until",
        ),
    )

    id: str = Field(
        default_factory=lambda: f"sfu-audience-{uuid.uuid4().hex}",
        primary_key=True,
    )
    audience_ref: str
    publication_ref: str
    audience_digest: str = Field(repr=False)
    policy_digest: str = Field(repr=False)
    membership_digest: str = Field(repr=False)
    policy_epoch: int
    membership_epoch: int
    key_epoch: int


class SfuReceiverGroupDB(_SfuBroadcastProjectionDB, table=True):
    """Digest-only projection of one canonical receiver subscription group."""

    __tablename__ = "sfu_receiver_groups"
    __table_args__ = _projection_constraints("sfu_receiver_group") + (
        sa.CheckConstraint(
            "membership_epoch >= 0 AND key_epoch >= 0 AND topology_epoch >= 0",
            name="ck_sfu_receiver_group_epochs_non_negative",
        ),
        sa.CheckConstraint(
            "length(group_digest) = 64 AND length(membership_digest) = 64 "
            "AND length(key_digest) = 64",
            name="ck_sfu_receiver_group_digest_length",
        ),
        sa.Index(
            "uq_sfu_receiver_group_active_subscription",
            "tenant_id",
            "session_id",
            "subscription_ref",
            unique=True,
            sqlite_where=_ACTIVE_PROJECTION,
            postgresql_where=_ACTIVE_PROJECTION,
        ),
        sa.Index(
            "ix_sfu_receiver_group_room_revision",
            "tenant_id",
            "session_id",
            "room_state_revision",
        ),
        sa.Index(
            "ix_sfu_receiver_group_retention",
            "status",
            "expires_at",
            "retain_until",
        ),
    )

    id: str = Field(
        default_factory=lambda: f"sfu-receiver-group-{uuid.uuid4().hex}",
        primary_key=True,
    )
    receiver_group_ref: str
    subscription_ref: str
    group_digest: str = Field(repr=False)
    membership_digest: str = Field(repr=False)
    key_digest: str = Field(repr=False)
    membership_epoch: int
    key_epoch: int
    topology_epoch: int


class SfuFanoutRouteDB(_SfuBroadcastProjectionDB, table=True):
    """Fenced route projection linking one audience to one receiver group."""

    __tablename__ = "sfu_fanout_routes"
    __table_args__ = _projection_constraints("sfu_fanout_route") + (
        sa.CheckConstraint(
            "policy_epoch >= 0 AND membership_epoch >= 0 AND key_epoch >= 0 "
            "AND route_epoch >= 0 AND topology_epoch >= 0",
            name="ck_sfu_fanout_route_epochs_non_negative",
        ),
        sa.CheckConstraint(
            "length(route_digest) = 64 AND length(policy_digest) = 64 "
            "AND length(membership_digest) = 64 AND length(key_digest) = 64",
            name="ck_sfu_fanout_route_digest_length",
        ),
        sa.ForeignKeyConstraint(
            ["audience_projection_id"],
            ["sfu_broadcast_audiences.id"],
            name="fk_sfu_fanout_route_audience_projection",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["receiver_group_projection_id"],
            ["sfu_receiver_groups.id"],
            name="fk_sfu_fanout_route_receiver_group_projection",
            ondelete="RESTRICT",
        ),
        sa.Index(
            "uq_sfu_fanout_route_active_edge",
            "tenant_id",
            "session_id",
            "publication_ref",
            "subscription_ref",
            unique=True,
            sqlite_where=_ACTIVE_PROJECTION,
            postgresql_where=_ACTIVE_PROJECTION,
        ),
        sa.Index(
            "ix_sfu_fanout_route_room_revision",
            "tenant_id",
            "session_id",
            "room_state_revision",
        ),
        sa.Index(
            "ix_sfu_fanout_route_fence",
            "tenant_id",
            "session_id",
            "route_epoch",
            "topology_epoch",
            "fencing_token",
        ),
        sa.Index(
            "ix_sfu_fanout_route_retention",
            "status",
            "expires_at",
            "retain_until",
        ),
    )

    id: str = Field(
        default_factory=lambda: f"sfu-fanout-route-{uuid.uuid4().hex}",
        primary_key=True,
    )
    route_ref: str
    audience_projection_id: str
    receiver_group_projection_id: str
    publication_ref: str
    subscription_ref: str
    route_digest: str = Field(repr=False)
    policy_digest: str = Field(repr=False)
    membership_digest: str = Field(repr=False)
    key_digest: str = Field(repr=False)
    policy_epoch: int
    membership_epoch: int
    key_epoch: int
    route_epoch: int
    topology_epoch: int


__all__ = [
    "SfuBroadcastAudienceDB",
    "SfuFanoutRouteDB",
    "SfuReceiverGroupDB",
]
