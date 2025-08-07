import controller.controller as cc
from src.db import get_conn


def test_add_task_and_next():
    with cc.app.test_client() as client:
        resp = client.post("/agent/add_task", json={"task": "t1", "agent": "default"})
        assert resp.status_code == 201
        resp = client.get("/tasks/next")
        assert resp.get_json()["task"] == "t1"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM controller.tasks")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT data FROM controller.config ORDER BY id DESC LIMIT 1")
    cfg = cur.fetchone()[0]
    cur.close()
    conn.close()
    assert cfg["agents"]["default"]["current_task"] == "t1"
