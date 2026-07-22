"""Add fenced SFU broadcast audience, receiver-group and route projections.

Revision ID: 3bf2c3d4e5f6
Revises: 2ae1f2a3b4c5
Create Date: 2026-07-22 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision: str = "3bf2c3d4e5f6"
down_revision: str | Sequence[str] | None = "2ae1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES = (
    "sfu_broadcast_audiences",
    "sfu_receiver_groups",
    "sfu_fanout_routes",
)
_LIVE_STATUSES = ("pending", "active", "draining")
_ACTIVE_WHERE = sa.text("status = 'active' AND tombstoned_at IS NULL")


def _common_columns() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("room_state_id", sa.String(), nullable=False),
        sa.Column("room_state_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("retention_seconds", sa.Integer(), nullable=False),
        sa.Column("retention_status", sa.String(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("retain_until", sa.Float(), nullable=False),
        sa.Column("tombstoned_at", sa.Float(), nullable=True),
        sa.Column("tombstone_reason", sa.String(), nullable=True),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("audit_actor_ref", sa.String(), nullable=False),
        sa.Column("audit_reason", sa.String(), nullable=False),
        sa.Column("request_digest", sa.String(), nullable=False),
        sa.Column("idempotency_key_digest", sa.String(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Column("audited_at", sa.Float(), nullable=False),
    ]


def _common_constraints(prefix: str) -> list[sa.CheckConstraint]:
    return [
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
    ]


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    if "semantic_sfu_room_states" not in existing:
        raise RuntimeError("semantic_sfu_room_states must exist before broadcast projections")

    if "sfu_broadcast_audiences" not in existing:
        op.create_table(
            "sfu_broadcast_audiences",
            sa.Column("id", sa.String(), nullable=False),
            *_common_columns(),
            sa.Column("audience_ref", sa.String(), nullable=False),
            sa.Column("publication_ref", sa.String(), nullable=False),
            sa.Column("audience_digest", sa.String(), nullable=False),
            sa.Column("policy_digest", sa.String(), nullable=False),
            sa.Column("membership_digest", sa.String(), nullable=False),
            sa.Column("policy_epoch", sa.Integer(), nullable=False),
            sa.Column("membership_epoch", sa.Integer(), nullable=False),
            sa.Column("key_epoch", sa.Integer(), nullable=False),
            *_common_constraints("sfu_broadcast_audience"),
            sa.CheckConstraint(
                "policy_epoch >= 0 AND membership_epoch >= 0 AND key_epoch >= 0",
                name="ck_sfu_broadcast_audience_epochs_non_negative",
            ),
            sa.CheckConstraint(
                "length(audience_digest) = 64 AND length(policy_digest) = 64 "
                "AND length(membership_digest) = 64",
                name="ck_sfu_broadcast_audience_digest_length",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "uq_sfu_broadcast_audience_active_publication",
            "sfu_broadcast_audiences",
            ["tenant_id", "session_id", "publication_ref"],
            unique=True,
            sqlite_where=_ACTIVE_WHERE,
            postgresql_where=_ACTIVE_WHERE,
        )
        op.create_index(
            "ix_sfu_broadcast_audience_room_revision",
            "sfu_broadcast_audiences",
            ["tenant_id", "session_id", "room_state_revision"],
        )
        op.create_index(
            "ix_sfu_broadcast_audience_retention",
            "sfu_broadcast_audiences",
            ["status", "expires_at", "retain_until"],
        )

    existing = set(inspect(bind).get_table_names())
    if "sfu_receiver_groups" not in existing:
        op.create_table(
            "sfu_receiver_groups",
            sa.Column("id", sa.String(), nullable=False),
            *_common_columns(),
            sa.Column("receiver_group_ref", sa.String(), nullable=False),
            sa.Column("subscription_ref", sa.String(), nullable=False),
            sa.Column("group_digest", sa.String(), nullable=False),
            sa.Column("membership_digest", sa.String(), nullable=False),
            sa.Column("key_digest", sa.String(), nullable=False),
            sa.Column("membership_epoch", sa.Integer(), nullable=False),
            sa.Column("key_epoch", sa.Integer(), nullable=False),
            sa.Column("topology_epoch", sa.Integer(), nullable=False),
            *_common_constraints("sfu_receiver_group"),
            sa.CheckConstraint(
                "membership_epoch >= 0 AND key_epoch >= 0 AND topology_epoch >= 0",
                name="ck_sfu_receiver_group_epochs_non_negative",
            ),
            sa.CheckConstraint(
                "length(group_digest) = 64 AND length(membership_digest) = 64 "
                "AND length(key_digest) = 64",
                name="ck_sfu_receiver_group_digest_length",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "uq_sfu_receiver_group_active_subscription",
            "sfu_receiver_groups",
            ["tenant_id", "session_id", "subscription_ref"],
            unique=True,
            sqlite_where=_ACTIVE_WHERE,
            postgresql_where=_ACTIVE_WHERE,
        )
        op.create_index(
            "ix_sfu_receiver_group_room_revision",
            "sfu_receiver_groups",
            ["tenant_id", "session_id", "room_state_revision"],
        )
        op.create_index(
            "ix_sfu_receiver_group_retention",
            "sfu_receiver_groups",
            ["status", "expires_at", "retain_until"],
        )

    existing = set(inspect(bind).get_table_names())
    if "sfu_fanout_routes" not in existing:
        op.create_table(
            "sfu_fanout_routes",
            sa.Column("id", sa.String(), nullable=False),
            *_common_columns(),
            sa.Column("route_ref", sa.String(), nullable=False),
            sa.Column("audience_projection_id", sa.String(), nullable=False),
            sa.Column("receiver_group_projection_id", sa.String(), nullable=False),
            sa.Column("publication_ref", sa.String(), nullable=False),
            sa.Column("subscription_ref", sa.String(), nullable=False),
            sa.Column("route_digest", sa.String(), nullable=False),
            sa.Column("policy_digest", sa.String(), nullable=False),
            sa.Column("membership_digest", sa.String(), nullable=False),
            sa.Column("key_digest", sa.String(), nullable=False),
            sa.Column("policy_epoch", sa.Integer(), nullable=False),
            sa.Column("membership_epoch", sa.Integer(), nullable=False),
            sa.Column("key_epoch", sa.Integer(), nullable=False),
            sa.Column("route_epoch", sa.Integer(), nullable=False),
            sa.Column("topology_epoch", sa.Integer(), nullable=False),
            *_common_constraints("sfu_fanout_route"),
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
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "uq_sfu_fanout_route_active_edge",
            "sfu_fanout_routes",
            ["tenant_id", "session_id", "publication_ref", "subscription_ref"],
            unique=True,
            sqlite_where=_ACTIVE_WHERE,
            postgresql_where=_ACTIVE_WHERE,
        )
        op.create_index(
            "ix_sfu_fanout_route_room_revision",
            "sfu_fanout_routes",
            ["tenant_id", "session_id", "room_state_revision"],
        )
        op.create_index(
            "ix_sfu_fanout_route_fence",
            "sfu_fanout_routes",
            ["tenant_id", "session_id", "route_epoch", "topology_epoch", "fencing_token"],
        )
        op.create_index(
            "ix_sfu_fanout_route_retention",
            "sfu_fanout_routes",
            ["status", "expires_at", "retain_until"],
        )

    _install_validation_triggers(bind)


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    for table in _TABLES:
        if table not in existing:
            continue
        live = bind.execute(
            sa.text(
                f"SELECT 1 FROM {table} "
                "WHERE status IN ('pending', 'active', 'draining') "
                "AND tombstoned_at IS NULL LIMIT 1"
            )
        ).first()
        if live is not None:
            raise RuntimeError(f"refusing to drop live SFU broadcast projections from {table}")

    for table in reversed(_TABLES):
        if table in existing:
            op.drop_table(table)

    if bind.dialect.name == "postgresql":
        for table in _TABLES:
            op.execute(
                sa.text(
                    f"DROP FUNCTION IF EXISTS validate_{table}_projection() CASCADE"
                )
            )


def _install_validation_triggers(bind: sa.engine.Connection) -> None:
    if bind.dialect.name == "sqlite":
        _install_sqlite_validation_triggers()
        return
    if bind.dialect.name == "postgresql":
        _install_postgresql_validation_triggers()
        return
    raise RuntimeError(
        f"SFU broadcast projection validation is unsupported on {bind.dialect.name}"
    )


def _sqlite_json_reference(collection: str, reference: str, key_name: str) -> str:
    camel = "publicationId" if key_name == "publication_id" else "subscriptionId"
    return f"""
        EXISTS (
            SELECT 1
            FROM json_each(s.{collection}) AS item
            WHERE item.key = NEW.{reference}
               OR item.atom = NEW.{reference}
               OR (
                    json_type(item.value) = 'object'
                    AND COALESCE(
                        json_extract(item.value, '$.{key_name}'),
                        json_extract(item.value, '$.{camel}'),
                        json_extract(item.value, '$.id')
                    ) = NEW.{reference}
               )
        )
    """


def _sqlite_room_reference(publication: bool, subscription: bool) -> str:
    predicates = [
        "s.id = NEW.room_state_id",
        "s.tenant_id = NEW.tenant_id",
        "s.session_id = NEW.session_id",
        "s.revision = NEW.room_state_revision",
    ]
    if publication:
        predicates.append(
            _sqlite_json_reference("publications", "publication_ref", "publication_id")
        )
    if subscription:
        predicates.append(
            _sqlite_json_reference("subscriptions", "subscription_ref", "subscription_id")
        )
    return " AND ".join(f"({predicate})" for predicate in predicates)


def _sqlite_parent_reference(table: str) -> str:
    if table != "sfu_fanout_routes":
        return "1 = 1"
    return """
        EXISTS (
            SELECT 1 FROM sfu_broadcast_audiences AS audience
            WHERE audience.id = NEW.audience_projection_id
              AND audience.tenant_id = NEW.tenant_id
              AND audience.session_id = NEW.session_id
              AND audience.room_state_id = NEW.room_state_id
              AND audience.room_state_revision = NEW.room_state_revision
              AND audience.publication_ref = NEW.publication_ref
              AND audience.status = 'active'
              AND audience.tombstoned_at IS NULL
        )
        AND EXISTS (
            SELECT 1 FROM sfu_receiver_groups AS receiver_group
            WHERE receiver_group.id = NEW.receiver_group_projection_id
              AND receiver_group.tenant_id = NEW.tenant_id
              AND receiver_group.session_id = NEW.session_id
              AND receiver_group.room_state_id = NEW.room_state_id
              AND receiver_group.room_state_revision = NEW.room_state_revision
              AND receiver_group.subscription_ref = NEW.subscription_ref
              AND receiver_group.status = 'active'
              AND receiver_group.tombstoned_at IS NULL
        )
    """


def _install_sqlite_validation_triggers() -> None:
    definitions = {
        "sfu_broadcast_audiences": (True, False, ("policy_epoch", "membership_epoch", "key_epoch")),
        "sfu_receiver_groups": (False, True, ("membership_epoch", "key_epoch", "topology_epoch")),
        "sfu_fanout_routes": (
            True,
            True,
            ("policy_epoch", "membership_epoch", "key_epoch", "route_epoch", "topology_epoch"),
        ),
    }
    for table, (publication, subscription, epochs) in definitions.items():
        room_reference = _sqlite_room_reference(publication, subscription)
        parent_reference = _sqlite_parent_reference(table)
        for operation in ("INSERT", "UPDATE"):
            trigger = f"trg_{table}_validate_{operation.lower()}"
            op.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger}"))
            op.execute(sa.text(f"""
                CREATE TRIGGER {trigger}
                BEFORE {operation} ON {table}
                FOR EACH ROW
                BEGIN
                    SELECT CASE WHEN NOT EXISTS (
                        SELECT 1 FROM semantic_sfu_room_states AS s
                        WHERE {room_reference}
                    ) THEN RAISE(ABORT, 'stale_or_orphan_semantic_sfu_reference') END;
                    SELECT CASE WHEN NOT ({parent_reference})
                        THEN RAISE(ABORT, 'orphan_sfu_broadcast_projection_reference') END;
                    SELECT CASE WHEN NEW.status = 'active'
                                      AND NEW.expires_at <= CAST(strftime('%s', 'now') AS REAL)
                        THEN RAISE(ABORT, 'sfu_broadcast_projection_expired') END;
                END
            """))

        monotone = " OR ".join(
            [
                "NEW.version <= OLD.version",
                "NEW.fencing_token < OLD.fencing_token",
                "NEW.room_state_revision < OLD.room_state_revision",
                *(f"NEW.{column} < OLD.{column}" for column in epochs),
            ]
        )
        trigger = f"trg_{table}_monotone_update"
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger}"))
        op.execute(sa.text(f"""
            CREATE TRIGGER {trigger}
            BEFORE UPDATE ON {table}
            FOR EACH ROW WHEN {monotone}
            BEGIN
                SELECT RAISE(ABORT, 'non_monotone_sfu_broadcast_projection_update');
            END
        """))


def _postgres_json_reference(collection: str, reference: str, key_name: str) -> str:
    camel = "publicationId" if key_name == "publication_id" else "subscriptionId"
    return f"""
        (
            (jsonb_typeof(s.{collection}::jsonb) = 'object'
             AND s.{collection}::jsonb ? NEW.{reference})
            OR EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                    CASE WHEN jsonb_typeof(s.{collection}::jsonb) = 'array'
                         THEN s.{collection}::jsonb ELSE '[]'::jsonb END
                ) AS item(value)
                WHERE COALESCE(
                    item.value ->> '{key_name}',
                    item.value ->> '{camel}',
                    item.value ->> 'id',
                    item.value #>> '{{}}'
                ) = NEW.{reference}
            )
        )
    """


def _postgres_room_reference(publication: bool, subscription: bool) -> str:
    predicates = [
        "s.id = NEW.room_state_id",
        "s.tenant_id = NEW.tenant_id",
        "s.session_id = NEW.session_id",
        "s.revision = NEW.room_state_revision",
    ]
    if publication:
        predicates.append(
            _postgres_json_reference("publications", "publication_ref", "publication_id")
        )
    if subscription:
        predicates.append(
            _postgres_json_reference("subscriptions", "subscription_ref", "subscription_id")
        )
    return " AND ".join(f"({predicate})" for predicate in predicates)


def _postgres_parent_reference(table: str) -> str:
    if table != "sfu_fanout_routes":
        return "TRUE"
    return """
        EXISTS (
            SELECT 1 FROM sfu_broadcast_audiences AS audience
            WHERE audience.id = NEW.audience_projection_id
              AND audience.tenant_id = NEW.tenant_id
              AND audience.session_id = NEW.session_id
              AND audience.room_state_id = NEW.room_state_id
              AND audience.room_state_revision = NEW.room_state_revision
              AND audience.publication_ref = NEW.publication_ref
              AND audience.status = 'active'
              AND audience.tombstoned_at IS NULL
        )
        AND EXISTS (
            SELECT 1 FROM sfu_receiver_groups AS receiver_group
            WHERE receiver_group.id = NEW.receiver_group_projection_id
              AND receiver_group.tenant_id = NEW.tenant_id
              AND receiver_group.session_id = NEW.session_id
              AND receiver_group.room_state_id = NEW.room_state_id
              AND receiver_group.room_state_revision = NEW.room_state_revision
              AND receiver_group.subscription_ref = NEW.subscription_ref
              AND receiver_group.status = 'active'
              AND receiver_group.tombstoned_at IS NULL
        )
    """


def _install_postgresql_validation_triggers() -> None:
    definitions = {
        "sfu_broadcast_audiences": (True, False, ("policy_epoch", "membership_epoch", "key_epoch")),
        "sfu_receiver_groups": (False, True, ("membership_epoch", "key_epoch", "topology_epoch")),
        "sfu_fanout_routes": (
            True,
            True,
            ("policy_epoch", "membership_epoch", "key_epoch", "route_epoch", "topology_epoch"),
        ),
    }
    for table, (publication, subscription, epochs) in definitions.items():
        function = f"validate_{table}_projection"
        monotone = " OR ".join(
            [
                "NEW.version <= OLD.version",
                "NEW.fencing_token < OLD.fencing_token",
                "NEW.room_state_revision < OLD.room_state_revision",
                *(f"NEW.{column} < OLD.{column}" for column in epochs),
            ]
        )
        op.execute(sa.text(f"""
            CREATE OR REPLACE FUNCTION {function}() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM semantic_sfu_room_states AS s
                    WHERE {_postgres_room_reference(publication, subscription)}
                ) THEN
                    RAISE EXCEPTION 'stale_or_orphan_semantic_sfu_reference'
                        USING ERRCODE = '23514';
                END IF;
                IF NOT ({_postgres_parent_reference(table)}) THEN
                    RAISE EXCEPTION 'orphan_sfu_broadcast_projection_reference'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.status = 'active'
                   AND NEW.expires_at <= EXTRACT(EPOCH FROM CURRENT_TIMESTAMP) THEN
                    RAISE EXCEPTION 'sfu_broadcast_projection_expired'
                        USING ERRCODE = '23514';
                END IF;
                IF TG_OP = 'UPDATE' AND ({monotone}) THEN
                    RAISE EXCEPTION 'non_monotone_sfu_broadcast_projection_update'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $$
        """))
        trigger = f"trg_{table}_validate"
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger} ON {table}"))
        op.execute(sa.text(f"""
            CREATE TRIGGER {trigger}
            BEFORE INSERT OR UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION {function}()
        """))
