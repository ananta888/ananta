import json
import controller.controller as cc

def setup(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"agents": {"default": {}}, "active_agent": "default", "tasks": []}))
    monkeypatch.setattr(cc, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cc, "CONFIG_FILE", str(cfg_file))
    cc.config_provider = cc.FileConfig(cc.read_config, cc.write_config)
    return cc.app.test_client(), cfg_file


def test_add_task_and_next_config(tmp_path, monkeypatch):
    client, cfg_file = setup(tmp_path, monkeypatch)
    resp = client.post("/agent/add_task", json={"task": "t1"})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["added"]["task"] == "t1"

    resp = client.get("/next-config")
    cfg = resp.get_json()
    assert cfg["tasks"] == ["t1"]

    saved = json.loads(cfg_file.read_text())
    assert saved["tasks"] == []
    assert saved["agents"]["default"]["current_task"] == "t1"


def test_agent_log_endpoint(tmp_path, monkeypatch):
    client, cfg_file = setup(tmp_path, monkeypatch)
    log = tmp_path / "ai_log_default.json"
    log.write_text("hello")
    resp = client.get("/agent/default/log")
    assert resp.status_code == 200
    assert resp.data.decode() == "hello"
