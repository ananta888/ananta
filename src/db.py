import psycopg2

try:  # Prefer shared configuration if available
    from .db_config import DATABASE_URL
except ImportError:  # pragma: no cover - fallback when run as a script
    from db_config import DATABASE_URL


def get_conn():
    return psycopg2.connect(DATABASE_URL)


SCHEMA_STATEMENTS = [
    "CREATE SCHEMA IF NOT EXISTS controller",
    "CREATE SCHEMA IF NOT EXISTS agent",
]
import os
import logging
import psycopg2
from psycopg2.extensions import connection

logger = logging.getLogger(__name__)

# Datenbank-Verbindungsstring aus Umgebungsvariable oder Standardwert
DATABASE_URL = os.environ.get(
    "DATABASE_URL", 
    "postgresql://postgres:postgres@db:5432/ananta"
)

def get_conn() -> connection:
    """Stellt eine Verbindung zur Datenbank her.

    Returns:
        Eine Datenbankverbindung
    """
    return psycopg2.connect(DATABASE_URL)

def init_db() -> None:
    """Initialisiert die Datenbankschemas und -tabellen."""
    try:
        conn = get_conn()
        cur = conn.cursor()

        # Schemas erstellen
        cur.execute("CREATE SCHEMA IF NOT EXISTS controller")
        cur.execute("CREATE SCHEMA IF NOT EXISTS agent")

        # Tabellen für Controller
        cur.execute("""
        CREATE TABLE IF NOT EXISTS controller.config (
            id SERIAL PRIMARY KEY,
            data JSONB NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS controller.blacklist (
            id SERIAL PRIMARY KEY,
            cmd TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS controller.control_log (
            id SERIAL PRIMARY KEY,
            received TEXT NOT NULL,
            summary TEXT,
            approved TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS controller.tasks (
            id SERIAL PRIMARY KEY,
            task TEXT NOT NULL,
            agent TEXT,
            template TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # Tabellen für Agent
        cur.execute("""
        CREATE TABLE IF NOT EXISTS agent.config (
            id SERIAL PRIMARY KEY,
            data JSONB NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS agent.logs (
            id SERIAL PRIMARY KEY,
            agent TEXT NOT NULL,
            level INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS agent.flags (
            name TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        conn.commit()
        logger.info("Datenbank erfolgreich initialisiert")
    except Exception as e:
        logger.error("Fehler bei der Datenbankinitialisierung: %s", e)
        raise
    finally:
        if 'cur' in locals():
            cur.close()
        if 'conn' in locals():
            conn.close()
TABLE_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS controller.config (
        id SERIAL PRIMARY KEY,
        data JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS controller.tasks (
        id SERIAL PRIMARY KEY,
        task TEXT,
        agent TEXT,
        template TEXT,
        created_at TIMESTAMP DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS controller.logs (
        id SERIAL PRIMARY KEY,
        agent TEXT,
        level TEXT,
        message TEXT,
        created_at TIMESTAMP DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS controller.blacklist (
        cmd TEXT PRIMARY KEY
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS controller.control_log (
        id SERIAL PRIMARY KEY,
        received TEXT,
        summary TEXT,
        approved TEXT,
        timestamp TIMESTAMP DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent.logs (
        id SERIAL PRIMARY KEY,
        agent TEXT,
        level TEXT,
        message TEXT,
        created_at TIMESTAMP DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent.config (
        id SERIAL PRIMARY KEY,
        data JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent.flags (
        name TEXT PRIMARY KEY,
        value TEXT
    )
    """,
]


def init_db():
    """Create required schemas and tables for controller and agent."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            for stmt in SCHEMA_STATEMENTS + TABLE_STATEMENTS:
                cur.execute(stmt)
        conn.commit()
