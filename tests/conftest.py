import os

os.environ.setdefault("DATABASE_URL", "postgresql://postgres@localhost:5432/ananta")

import pytest
from psycopg2.extras import Json

from src.db import get_conn, init_db
import controller.controller as cc


@pytest.fixture(autouse=True)
def clean_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost:5432/ananta")
    init_db()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("TRUNCATE controller.config, controller.tasks, controller.logs, controller.blacklist, controller.control_log RESTART IDENTITY CASCADE")
    cur.execute("TRUNCATE agent.config, agent.logs, agent.flags RESTART IDENTITY CASCADE")
    cur.execute(
        "INSERT INTO controller.config (data) VALUES (%s)",
        (
            Json(
                {
                    "active_agent": "default",
                    "agents": {},
                    "api_endpoints": [],
                    "prompt_templates": {},
                }
            ),
        ),
    )
    conn.commit()
    cur.close()
    conn.close()
    cc.config_manager = cc.ConfigManager(cc.CONFIG_PATH)
    cc.task_store = cc.TaskStore()
    yield
