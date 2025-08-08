from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from psycopg2.extras import Json

from src.db import get_conn, init_db


class ConfigManager:
    """Persist configuration in PostgreSQL."""

    def __init__(self, default_path: str | Path):
        self.default_path = Path(default_path)
        # Wir rufen init_db nicht mehr hier auf, sondern im entrypoint-ai-agent.sh
        # über das separate db_setup.py Skript

    def read(self) -> Dict[str, Any]:
        max_retries = 5
        for attempt in range(max_retries):
            try:
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("SELECT data FROM controller.config ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                if row is None:
                    if self.default_path.exists():
                        with self.default_path.open("r", encoding="utf-8") as fh:
                            data = json.load(fh)
                    else:
                        data = {}
                    # Stellt sicher, dass das Schema existiert
                    cur.execute("CREATE SCHEMA IF NOT EXISTS controller")
                    conn.commit()
                    # Stellt sicher, dass die Tabelle existiert
                    cur.execute("""
                    CREATE TABLE IF NOT EXISTS controller.config (
                        id SERIAL PRIMARY KEY,
                        data JSONB
                    )
                    """)
                    conn.commit()
                    cur.execute(
                        "INSERT INTO controller.config (data) VALUES (%s)",
                        (Json(data),),
                    )
                    conn.commit()
                else:
                    data = row[0]
                cur.close()
                conn.close()
                return data
            except Exception as e:
                import logging
                logging.warning(f"Fehler beim Lesen der Konfiguration (Versuch {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    import time
                    time.sleep(1)
                else:
                    # Fallback: Wenn nach allen Versuchen kein Zugriff auf die DB möglich ist
                    if self.default_path.exists():
                        with self.default_path.open("r", encoding="utf-8") as fh:
                            return json.load(fh)
                    return {}

    def write(self, data: Dict[str, Any]) -> None:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                conn = get_conn()
                cur = conn.cursor()
                # Stelle sicher, dass die Tabelle existiert
                cur.execute("CREATE SCHEMA IF NOT EXISTS controller")
                conn.commit()
                cur.execute("""
                CREATE TABLE IF NOT EXISTS controller.config (
                    id SERIAL PRIMARY KEY,
                    data JSONB
                )
                """)
                conn.commit()
                cur.execute(
                    "INSERT INTO controller.config (data) VALUES (%s)",
                    (Json(data),),
                )
                conn.commit()
                cur.close()
                conn.close()
                return
            except Exception as e:
                import logging
                logging.warning(f"Fehler beim Schreiben der Konfiguration (Versuch {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    import time
                    time.sleep(1)
                else:
                    # Fallback: Speichere in die JSON-Datei
                    import logging
                    logging.error(f"Konnte Konfiguration nicht in DB speichern, Fallback auf Datei {self.default_path}")
                    with self.default_path.open("w", encoding="utf-8") as fh:
                        json.dump(data, fh, indent=2)
