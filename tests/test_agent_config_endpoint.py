import importlib

from src.db import get_conn


def test_agent_config_endpoint(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost:5432/ananta")
    ai = importlib.reload(importlib.import_module("agent.ai_agent"))
    client = ai.app.test_client()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("TRUNCATE agent.config RESTART IDENTITY CASCADE")
    conn.commit()
    cur.close()
    conn.close()

    resp = client.get("/agent/config")
    assert resp.status_code == 200
    assert resp.get_json() == {}

    payload = {"example": 1}
    post = client.post("/agent/config", json=payload)
    assert post.status_code == 200
    assert post.get_json()["status"] == "ok"

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT data FROM agent.config ORDER BY id DESC LIMIT 1")
    stored = cur.fetchone()[0]
    cur.close()
    conn.close()
    assert stored == payload

    resp2 = client.get("/agent/config")
    assert resp2.get_json() == payload
