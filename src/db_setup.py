#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Datenbankeinrichtungsskript für das Ananta-System

import logging
import time

import psycopg2

try:  # Use shared configuration so all modules reference the same database
    from .db_config import DATABASE_URL
    from .db import init_db
except ImportError:  # pragma: no cover - fallback when executed directly
    from db_config import DATABASE_URL
    from db import init_db
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

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Warte auf Datenbankverbindung...")
    if wait_for_db():
        print("Datenbank ist bereit!")
        print("Initialisiere Datenbankschemas...")
        try:
            init_db()
            logger.info("Datenbank-Schemas und Tabellen wurden erfolgreich eingerichtet")
        except Exception as e:
            logger.error(f"Fehler beim Einrichten der Datenbank: {e}")
            raise
    else:
        exit(1)
