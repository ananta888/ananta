#!/usr/bin/env python3

import psycopg2
import time
import sys
import logging
from pathlib import Path

# Projekt-Root zum sys.path hinzufügen, damit 'src' gefunden wird
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from src.config.settings import settings

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('wait-for-db')

def wait_for_db():
    """Warte auf die Datenbank mit Wiederholungsversuchen."""
    db_url = settings.database_url
    max_retries = settings.db_wait_retries
    delay = settings.db_wait_delay
    
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Versuch {attempt}/{max_retries}: Verbindung zur Datenbank herstellen...")
            conn = psycopg2.connect(db_url)
            conn.close()
            logger.info(f"Datenbankverbindung erfolgreich nach {attempt} Versuchen")
            return True
        except Exception as e:
            logger.warning(f"Verbindung zur Datenbank nicht möglich: {e}")
            if attempt < max_retries:
                logger.info(f"Warte {delay} Sekunden vor dem nächsten Versuch...")
                time.sleep(delay)

    logger.error(f"Konnte keine Verbindung zur Datenbank herstellen nach {max_retries} Versuchen")
    return False

if __name__ == "__main__":
    if wait_for_db():
        sys.exit(0)
    else:
        sys.exit(1)
