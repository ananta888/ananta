from __future__ import annotations

import logging

from src.db import get_conn, init_db


class DBLogHandler(logging.Handler):
    def __init__(self, schema: str):
        super().__init__()
        self.schema = schema
        init_db()

    def emit(self, record: logging.LogRecord) -> None:
        conn = get_conn()
        cur = conn.cursor()
        agent = getattr(record, "agent", None)
        cur.execute(
            f"INSERT INTO {self.schema}.logs (agent, level, message) VALUES (%s, %s, %s)",
            (agent, record.levelname, self.format(record)),
        )
        conn.commit()
        cur.close()
        conn.close()


class LogManager:
    """Configure logging to write into PostgreSQL."""

    @staticmethod
    def setup(schema: str) -> None:
        handler = DBLogHandler(schema)
        logging.basicConfig(level=logging.INFO, handlers=[handler])
