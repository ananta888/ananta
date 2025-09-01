"""PostgreSQL helper functions used by controller and agent."""

from __future__ import annotations

import logging
import os
from typing import Any

try:  # Guard import to avoid ImportError during test discovery
    import psycopg2  # type: ignore
    from psycopg2.extensions import connection as _PGConnType  # type: ignore
    _PG_AVAILABLE = True
except Exception:  # pragma: no cover
    psycopg2 = None  # type: ignore
    _PGConnType = Any  # type: ignore
    _PG_AVAILABLE = False

logger = logging.getLogger(__name__)

# Default connection string matches the docker-compose setup
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@db:5432/ananta",
)


def get_conn() -> _PGConnType:
    """Return a new database connection.
    Raises ImportError if psycopg2 is not available.
    """
    if not _PG_AVAILABLE:
        raise ImportError("psycopg2 is required to use the database. Install psycopg2-binary.")
    return psycopg2.connect(DATABASE_URL)


def init_db() -> None:
    """Create required schemas and tables if they do not yet exist."""

    conn = get_conn()
    cur = conn.cursor()
    try:
        # Schemas
        cur.execute("CREATE SCHEMA IF NOT EXISTS controller")
        cur.execute("CREATE SCHEMA IF NOT EXISTS agent")

        # Controller tables
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS controller.config (
                id SERIAL PRIMARY KEY,
                data JSONB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS controller.blacklist (
                id SERIAL PRIMARY KEY,
                cmd TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS controller.control_log (
                id SERIAL PRIMARY KEY,
                received TEXT NOT NULL,
                summary TEXT,
                approved TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS controller.tasks (
                id SERIAL PRIMARY KEY,
                task TEXT NOT NULL,
                agent TEXT,
                template TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Enhance tasks table with status/audit columns (idempotent)
        cur.execute("ALTER TABLE controller.tasks ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'queued'")
        cur.execute("ALTER TABLE controller.tasks ADD COLUMN IF NOT EXISTS log JSONB DEFAULT '[]'::jsonb")
        cur.execute("ALTER TABLE controller.tasks ADD COLUMN IF NOT EXISTS created_by TEXT")
        cur.execute("ALTER TABLE controller.tasks ADD COLUMN IF NOT EXISTS picked_by TEXT")
        cur.execute("ALTER TABLE controller.tasks ADD COLUMN IF NOT EXISTS picked_at TIMESTAMP")
        cur.execute("ALTER TABLE controller.tasks ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP")
        cur.execute("ALTER TABLE controller.tasks ADD COLUMN IF NOT EXISTS fail_count INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE controller.tasks ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_tasks_agent_status_created ON controller.tasks (agent, status, created_at)")

        # Agent tables
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS agent.config (
                id SERIAL PRIMARY KEY,
                data JSONB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS agent.logs (
                id SERIAL PRIMARY KEY,
                agent TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS agent.flags (
                name TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.commit()
        logger.info("Datenbank erfolgreich initialisiert")
    finally:
        cur.close()
        conn.close()

