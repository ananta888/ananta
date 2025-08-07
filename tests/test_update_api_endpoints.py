import controller.controller as cc
from src.db import get_conn


def test_update_api_endpoints():
    with cc.app.test_client() as client:
        resp = client.post(
            '/config/api_endpoints',
            json={"api_endpoints": [{"type": "x", "url": "y", "models": ["m1", "m2"]}]}
        )
        assert resp.status_code == 200
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT data FROM controller.config ORDER BY id DESC LIMIT 1")
    cfg = cur.fetchone()[0]
    cur.close()
    conn.close()
    assert cfg["api_endpoints"] == [{"type": "x", "url": "y", "models": ["m1", "m2"]}]
