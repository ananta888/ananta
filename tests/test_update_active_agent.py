from psycopg2.extras import Json
import controller.controller as cc
from src.db import get_conn


def test_update_active_agent():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO controller.config (data) VALUES (%s)",
        (
            Json(
                {
                    "agents": {"A": {}, "B": {}},
                    "active_agent": "A",
                    "api_endpoints": [],
                    "prompt_templates": {},
                }
            ),
        ),
    )
    conn.commit()
    cur.close()
    conn.close()
    with cc.app.test_client() as client:
        resp = client.post('/config/active_agent', json={"active_agent": "B"})
        assert resp.status_code == 200
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT data FROM controller.config ORDER BY id DESC LIMIT 1")
    cfg = cur.fetchone()[0]
    cur.close()
    conn.close()
    assert cfg["active_agent"] == "B"
