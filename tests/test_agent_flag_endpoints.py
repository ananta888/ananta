import importlib

from src.db import get_conn


def test_stop_and_restart_endpoints(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost:5432/ananta")
    ai = importlib.reload(importlib.import_module("agent.ai_agent"))
    client = ai.app.test_client()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("TRUNCATE agent.flags RESTART IDENTITY CASCADE")
    conn.commit()
    cur.close()
    conn.close()

    health = client.get("/health")
    assert health.status_code == 200
    assert health.get_json()["status"] == "ok"

    stop_resp = client.post("/stop")
    assert stop_resp.status_code == 200

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT value FROM agent.flags WHERE name='stop'")
    assert cur.fetchone()[0] == "1"
    cur.close()
    conn.close()

    restart_resp = client.post("/restart")
    assert restart_resp.status_code == 200

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agent.flags WHERE name='stop'")
    assert cur.fetchone() is None
    cur.close()
    conn.close()

