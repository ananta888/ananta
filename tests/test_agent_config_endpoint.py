import json
import importlib


def test_agent_config_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ai = importlib.reload(importlib.import_module("agent.ai_agent"))
    client = ai.app.test_client()

    resp = client.get("/agent/config")
    assert resp.status_code == 200
    assert resp.get_json() == {}

    payload = {"example": 1}
    post = client.post("/agent/config", json=payload)
    assert post.status_code == 200
    assert post.get_json()["status"] == "ok"
    stored = json.loads((tmp_path / "agent_config.json").read_text())
    assert stored == payload

    resp2 = client.get("/agent/config")
    assert resp2.get_json() == payload

