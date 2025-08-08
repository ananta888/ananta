"""PostgreSQL helper functions used by controller and agent."""

from __future__ import annotations

import logging
import os

import psycopg2
from psycopg2.extensions import connection

logger = logging.getLogger(__name__)

# Default connection string matches the docker-compose setup
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@db:5432/ananta",
)


def get_conn() -> connection:
    """Return a new database connection."""

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
                level INTEGER NOT NULL,
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

