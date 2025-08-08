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
