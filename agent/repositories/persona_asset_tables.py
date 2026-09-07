"""Identical CAS/audit schema, separate media namespaces; no migration."""

from sqlalchemy import BigInteger, Column, MetaData, String, Table, Text


def persona_asset_tables(kind):
    if kind not in ("image", "video", "voice"):
        raise ValueError("persona_asset_catalog_kind_invalid")
    metadata = MetaData()
    assets = Table(
        f"persona_{kind}_assets",
        metadata,
        Column("tenant_id", String(160), primary_key=True),
        Column("project_id", String(160), primary_key=True),
        Column("artifact_id", String(160), primary_key=True),
        Column("revision", BigInteger, nullable=False),
        Column("state", String(16), nullable=False),
        Column("payload", Text, nullable=False),
        Column("payload_sha256", String(64), nullable=False),
    )
    events = Table(
        f"persona_{kind}_asset_events",
        metadata,
        Column("tenant_id", String(160), primary_key=True),
        Column("project_id", String(160), primary_key=True),
        Column("artifact_id", String(160), primary_key=True),
        Column("revision", BigInteger, primary_key=True),
        Column("actor", String(255), nullable=False),
        Column("state", String(16), nullable=False),
    )
    return metadata, assets, events
