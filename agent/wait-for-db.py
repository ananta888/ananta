#!/usr/bin/env python3

import psycopg2
import os
import time
import sys
import logging

DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@db:5432/postgres')
MAX_RETRIES = 30
DELAY = 2

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('wait-for-db')

def wait_for_db():
    """Warte auf die Datenbank mit Wiederholungsversuchen."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(f"Versuch {attempt}/{MAX_RETRIES}: Verbindung zur Datenbank herstellen...")
            conn = psycopg2.connect(DATABASE_URL)
            conn.close()
            logger.info(f"Datenbankverbindung erfolgreich nach {attempt} Versuchen")
            return True
        except Exception as e:
            logger.warning(f"Verbindung zur Datenbank nicht möglich: {e}")
            if attempt < MAX_RETRIES:
                logger.info(f"Warte {DELAY} Sekunden vor dem nächsten Versuch...")
                time.sleep(DELAY)

    logger.error(f"Konnte keine Verbindung zur Datenbank herstellen nach {MAX_RETRIES} Versuchen")
    return False

if __name__ == "__main__":
    if wait_for_db():
        sys.exit(0)
    else:
        sys.exit(1)
