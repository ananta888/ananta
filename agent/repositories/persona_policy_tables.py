"""Separate image/video policy namespaces with unchanged revision-CAS schema."""

from sqlalchemy import BigInteger, Column, MetaData, String, Table, Text


def persona_policy_tables(kind):
    if kind not in ("image", "video"):
        raise ValueError("persona_policy_catalog_kind_invalid")
    metadata = MetaData()
    heads = Table(
        f"persona_{kind}_policy_heads",
        metadata,
        Column("tenant_id", String(160), primary_key=True),
        Column("project_id", String(160), primary_key=True),
        Column("source_id", String(160), primary_key=True),
        Column("policy_binding", String(160), nullable=False),
        Column("revision", BigInteger, nullable=False),
    )
    versions = Table(
        f"persona_{kind}_policy_versions",
        metadata,
        Column("tenant_id", String(160), primary_key=True),
        Column("project_id", String(160), primary_key=True),
        Column("policy_binding", String(160), primary_key=True),
        Column("revision", BigInteger, primary_key=True),
        Column("state", String(16), nullable=False),
        Column("payload", Text, nullable=False),
        Column("payload_sha256", String(64), nullable=False),
        Column("created_by", String(255), nullable=False),
        Column("revoked_by", String(255), nullable=True),
    )
    return metadata, heads, versions
