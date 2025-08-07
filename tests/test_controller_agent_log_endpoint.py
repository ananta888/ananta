import importlib

from src.db import get_conn


def test_delete_agent_log_controller(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost:5432/ananta")
    import controller.controller as cc
    importlib.reload(cc)
    client = cc.app.test_client()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO agent.logs (agent, level, message) VALUES (%s, %s, %s)",
        ("test", "INFO", "hi"),
    )
    conn.commit()
    cur.close()
    conn.close()

    resp = client.delete("/agent/test/log")
    assert resp.status_code == 204

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM agent.logs WHERE agent='test'")
    assert cur.fetchall() == []
    cur.close()
    conn.close()
