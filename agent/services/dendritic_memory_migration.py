"""Additive SQLite schema migration owned by the experimental subsystem."""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1


def upgrade_job_store(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS dendritic_job_revisions(tenant_id TEXT NOT NULL,run_id TEXT NOT NULL,"
        "revision INTEGER NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(tenant_id,run_id,revision))"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS dendritic_idempotency(tenant_id TEXT NOT NULL,key_digest TEXT NOT NULL,"
        "run_id TEXT NOT NULL,PRIMARY KEY(tenant_id,key_digest))"
    )
    _record_version(connection, "job-store")


def downgrade_job_store(connection: sqlite3.Connection) -> None:
    connection.execute("DROP TABLE IF EXISTS dendritic_idempotency")
    connection.execute("DROP TABLE IF EXISTS dendritic_job_revisions")
    _remove_version(connection, "job-store")


def upgrade_registry_store(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS dendritic_pack_revisions(tenant_id TEXT NOT NULL,pack_digest TEXT NOT NULL,"
        "revision INTEGER NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(tenant_id,pack_digest,revision))"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS dendritic_runtime_routes(tenant_id TEXT NOT NULL,scope_id TEXT NOT NULL,"
        "revision INTEGER NOT NULL,pack_digest TEXT NOT NULL,active INTEGER NOT NULL,payload_json TEXT NOT NULL,"
        "PRIMARY KEY(tenant_id,scope_id,revision))"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS dendritic_registry_idempotency(tenant_id TEXT NOT NULL,key_digest TEXT NOT NULL,"
        "payload_json TEXT NOT NULL,PRIMARY KEY(tenant_id,key_digest))"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS dendritic_registry_audit(sequence INTEGER PRIMARY KEY AUTOINCREMENT,"
        "tenant_id TEXT NOT NULL,payload_json TEXT NOT NULL)"
    )
    _record_version(connection, "registry-store")


def downgrade_registry_store(connection: sqlite3.Connection) -> None:
    for table in (
        "dendritic_registry_audit",
        "dendritic_registry_idempotency",
        "dendritic_runtime_routes",
        "dendritic_pack_revisions",
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table}")
    _remove_version(connection, "registry-store")


def _record_version(connection: sqlite3.Connection, component: str) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS dendritic_schema_versions(component TEXT PRIMARY KEY,version INTEGER NOT NULL)"
    )
    connection.execute(
        "INSERT INTO dendritic_schema_versions(component,version) VALUES(?,?) "
        "ON CONFLICT(component) DO UPDATE SET version=excluded.version",
        (component, SCHEMA_VERSION),
    )


def _remove_version(connection: sqlite3.Connection, component: str) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS dendritic_schema_versions(component TEXT PRIMARY KEY,version INTEGER NOT NULL)"
    )
    connection.execute("DELETE FROM dendritic_schema_versions WHERE component=?", (component,))


__all__ = [
    "SCHEMA_VERSION",
    "downgrade_job_store",
    "downgrade_registry_store",
    "upgrade_job_store",
    "upgrade_registry_store",
]
