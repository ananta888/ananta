from psycopg2.extras import Json

import controller.controller as cc
from src.db import get_conn


def test_agent_tasks_endpoint():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO controller.config (data) VALUES (%s)",
        (
            Json(
                {
                    "active_agent": "default",
                    "agents": {"default": {"current_task": "c"}},
                    "api_endpoints": [],
                    "prompt_templates": {},
                }
            ),
        ),
    )
    cur.execute(
        "INSERT INTO controller.tasks (task, agent) VALUES ('t1','default'), ('t2','other')"
    )
    conn.commit()
    cur.close()
    conn.close()
    with cc.app.test_client() as client:
        resp = client.get("/agent/default/tasks")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["current_task"] == "c"
        assert data["tasks"] == [{"task": "t1", "agent": "default", "template": None}]
