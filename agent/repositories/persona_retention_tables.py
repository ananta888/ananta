"""Separate image/video retention tables; existing image schema stays unchanged."""

from sqlalchemy import BigInteger, Column, Index, MetaData, String, Table


def persona_retention_tables(kind):
    if type(kind) is not str or kind not in ("image", "video", "voice"):
        raise ValueError("persona_retention_kind_invalid")
    _metadata = MetaData()
    retention = Table(
        f"persona_{kind}_retention",
        _metadata,
        *(Column(name, String(160), primary_key=True) for name in ("tenant_id", "project_id", "artifact_id")),
        Column("revision", BigInteger, nullable=False),
        Column("asset_revision", BigInteger, nullable=False),
        Column("asset_digest", String(64), nullable=False),
        Column("actor", String(255), nullable=False),
        Column("due_at_ms", BigInteger, nullable=False),
        Column("next_attempt_ms", BigInteger, nullable=False),
        Column("state", String(16), nullable=False),
        Column("attempts", BigInteger, nullable=False),
        Column("task_id", String(160)),
        Column("lease_id", String(36)),
        Column("lease_until_ms", BigInteger, nullable=False),
    )
    events = Table(
        f"persona_{kind}_retention_events",
        _metadata,
        Column("event_id", String(36), primary_key=True),
        *(Column(name, String(160), nullable=False) for name in ("tenant_id", "project_id", "artifact_id")),
        Column("revision", BigInteger, nullable=False),
        Column("actor", String(255), nullable=False),
        Column("grant_actor", String(255), nullable=False),
        Column("asset_revision", BigInteger, nullable=False),
        Column("asset_digest", String(64), nullable=False),
        Column("due_at_ms", BigInteger, nullable=False),
        Column("lease_id", String(36)),
        Column("lease_until_ms", BigInteger, nullable=False),
        Column("state", String(16), nullable=False),
        Column("task_id", String(160)),
    )
    Index(
        "ix_persona_retention_due" if kind == "image" else f"ix_persona_{kind}_retention_due",
        retention.c.state,
        retention.c.next_attempt_ms,
    )

    return _metadata, retention, events
