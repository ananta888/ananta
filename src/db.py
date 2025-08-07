import os
import json
import psycopg2
from psycopg2.extras import Json

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@db:5432/ananta")


    def get_conn(retries=3, delay=1.0):
    """Versucht eine Verbindung zur Datenbank herzustellen, mit Wiederholungsversuchen.

    Parameters
    ----------
    retries : int
        Anzahl der Verbindungsversuche bevor ein Fehler geworfen wird
    delay : float
        Verzögerung in Sekunden zwischen Verbindungsversuchen

    Returns
    -------
    connection
        Eine aktive Datenbankverbindung

    Raises
    ------
    psycopg2.OperationalError
        Wenn nach allen Versuchen keine Verbindung hergestellt werden konnte
    """
    last_error = None
    for attempt in range(retries):
        try:
            return psycopg2.connect(DATABASE_URL)
        except psycopg2.OperationalError as e:
            last_error = e
            if attempt < retries - 1:
                import time
                import logging
                logging.warning(f"Datenbankverbindungsfehler (Versuch {attempt+1}/{retries}): {e}")
                time.sleep(delay)

    # Alle Versuche fehlgeschlagen
    if last_error:
        raise last_error
    else:
        # Sollte nicht passieren, aber zur Sicherheit
        raise psycopg2.OperationalError("Konnte keine Verbindung zur Datenbank herstellen")


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    # create schemas
    cur.execute("CREATE SCHEMA IF NOT EXISTS controller")
    cur.execute("CREATE SCHEMA IF NOT EXISTS agent")
    # controller tables
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS controller.config (
            id SERIAL PRIMARY KEY,
            data JSONB
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS controller.tasks (
            id SERIAL PRIMARY KEY,
            task TEXT,
            agent TEXT,
            template TEXT,
            created_at TIMESTAMP DEFAULT now()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS controller.logs (
            id SERIAL PRIMARY KEY,
            agent TEXT,
            level TEXT,
            message TEXT,
            created_at TIMESTAMP DEFAULT now()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS controller.blacklist (
            cmd TEXT PRIMARY KEY
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS controller.control_log (
            id SERIAL PRIMARY KEY,
            received TEXT,
            summary TEXT,
            approved TEXT,
            timestamp TIMESTAMP DEFAULT now()
        )
        """
    )
    # agent tables
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agent.logs (
            id SERIAL PRIMARY KEY,
            agent TEXT,
            level TEXT,
            message TEXT,
            created_at TIMESTAMP DEFAULT now()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agent.config (
            id SERIAL PRIMARY KEY,
            data JSONB
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agent.flags (
            name TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.commit()
    cur.close()
    conn.close()
