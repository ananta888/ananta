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
        init_db()

    def read(self) -> Dict[str, Any]:
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

    def write(self, data: Dict[str, Any]) -> None:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO controller.config (data) VALUES (%s)",
            (Json(data),),
        )
        conn.commit()
        cur.close()
        conn.close()
