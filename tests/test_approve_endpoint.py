import importlib

from src.db import get_conn


def test_approve_writes_log_and_blacklist(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost:5432/ananta")
    cc = importlib.reload(importlib.import_module("agent.ai_agent"))
    client = cc.app.test_client()
    resp = client.post("/approve", data={"cmd": "ls", "summary": "list"})
    assert resp.status_code == 200
    assert resp.data.decode() == "ls"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT cmd FROM controller.blacklist")
    blacklist = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT received, summary FROM controller.control_log")
    control = cur.fetchall()
    cur.close()
    conn.close()
    assert blacklist == ["ls"]
    assert ("ls", "list") in control
