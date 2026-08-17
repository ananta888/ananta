"""Versioned relational snapshot schema for CodeCompass DuckDB files."""

from __future__ import annotations

SCHEMA_VERSION = "ananta.codecompass_duckdb.v1"

DDL_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS snapshot_meta (
        schema_version VARCHAR NOT NULL,
        workspace_id VARCHAR NOT NULL,
        repository_id VARCHAR NOT NULL,
        profile_name VARCHAR NOT NULL,
        domain VARCHAR NOT NULL,
        manifest_hash VARCHAR NOT NULL,
        compatibility_fingerprint VARCHAR NOT NULL,
        source_revision VARCHAR NOT NULL,
        created_at VARCHAR NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS documents (
        record_id VARCHAR PRIMARY KEY,
        workspace_id VARCHAR NOT NULL,
        repository_id VARCHAR NOT NULL,
        profile_name VARCHAR NOT NULL,
        domain VARCHAR NOT NULL,
        path VARCHAR NOT NULL,
        kind VARCHAR NOT NULL,
        symbol VARCHAR,
        text VARCHAR,
        source_hash VARCHAR NOT NULL,
        manifest_hash VARCHAR NOT NULL,
        tombstone BOOLEAN NOT NULL DEFAULT FALSE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS vectors (
        record_id VARCHAR PRIMARY KEY,
        dimensions INTEGER NOT NULL,
        embedding FLOAT[] NOT NULL,
        model VARCHAR NOT NULL,
        distance VARCHAR NOT NULL,
        source_hash VARCHAR NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS graph_nodes (
        node_id VARCHAR PRIMARY KEY,
        kind VARCHAR NOT NULL,
        path VARCHAR,
        title VARCHAR,
        workspace_id VARCHAR NOT NULL,
        repository_id VARCHAR NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS graph_edges (
        edge_id VARCHAR PRIMARY KEY,
        source_id VARCHAR NOT NULL,
        target_id VARCHAR NOT NULL,
        relation VARCHAR NOT NULL,
        origin VARCHAR NOT NULL
    )
    """,
)


def apply_schema(connection) -> None:
    for statement in DDL_STATEMENTS:
        connection.execute(statement)


def schema_version() -> str:
    return SCHEMA_VERSION
