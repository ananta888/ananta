import psycopg2
import os
import time
import logging

DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@db:5432/postgres')
logger = logging.getLogger(__name__)

def wait_for_db(max_retries=30, delay=2):
    """Warte auf die Datenbank mit Wiederholungsversuchen."""
    for attempt in range(1, max_retries + 1):
        try:
            conn = psycopg2.connect(DATABASE_URL)
            conn.close()
            logger.info(f"Datenbankverbindung erfolgreich nach {attempt} Versuchen")
            return True
        except Exception as e:
            logger.warning(f"Versuch {attempt}/{max_retries}: Verbindung zur Datenbank nicht möglich: {e}")
            if attempt < max_retries:
                time.sleep(delay)
    logger.error(f"Konnte keine Verbindung zur Datenbank herstellen nach {max_retries} Versuchen")
    return False

def setup_db_schemas():
    """Erstelle die benötigten Schemas und Tabellen in der Datenbank."""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True  # Notwendig für CREATE SCHEMA
        cur = conn.cursor()

        # Schemas erstellen
        cur.execute("CREATE SCHEMA IF NOT EXISTS controller")
        cur.execute("CREATE SCHEMA IF NOT EXISTS agent")

        # Controller-Tabellen
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
            cmd TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS controller.control_log (
            id SERIAL PRIMARY KEY,
            received TEXT NOT NULL,
            summary TEXT,
            approved TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS controller.tasks (
            id SERIAL PRIMARY KEY,
            task TEXT NOT NULL,
            agent TEXT,
            template TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
        """)

        # Agent-Tabellen
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
        CREATE TABLE IF NOT EXISTS agent.config (
            id SERIAL PRIMARY KEY,
            data JSONB NOT NULL,
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

        logger.info("Datenbank-Schemas und Tabellen wurden erfolgreich eingerichtet")
    except Exception as e:
        logger.error(f"Fehler beim Einrichten der Datenbank: {e}")
        raise
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if wait_for_db():
        setup_db_schemas()
    else:
        exit(1)
