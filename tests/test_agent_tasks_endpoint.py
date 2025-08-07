import json
import controller.controller as cc

def setup(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "agents": {"default": {"current_task": "c"}},
        "tasks": [
            {"task": "t1", "agent": "default"},
            {"task": "t2", "agent": "other"}
        ]
    }))
    monkeypatch.setattr(cc, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cc, "CONFIG_FILE", str(cfg_file))
    cc.config_provider = cc.FileConfig(cc.read_config, cc.write_config)
    return cc.app.test_client()

def test_agent_tasks_endpoint(tmp_path, monkeypatch):
    client = setup(tmp_path, monkeypatch)
    resp = client.get("/agent/default/tasks")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["current_task"] == "c"
    assert data["tasks"] == [{"task": "t1", "agent": "default"}]
