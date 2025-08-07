import json
import yaml
import controller.controller as cc


def setup(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("active_agent: default\nagents:\n  default:\n    current_task: c\napi_endpoints: []\nprompt_templates: {}\n")
    tasks_file = tmp_path / "tasks.json"
    tasks_file.write_text(json.dumps([
        {"task": "t1", "agent": "default"},
        {"task": "t2", "agent": "other"},
    ]))
    monkeypatch.setattr(cc, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cc, "CONFIG_FILE", str(cfg_file))
    monkeypatch.setattr(cc, "TASKS_FILE", str(tasks_file))
    cc.config_manager = cc.ConfigManager(cfg_file)
    cc.task_store = cc.TaskStore(tasks_file)
    return cc.app.test_client()


def test_agent_tasks_endpoint(tmp_path, monkeypatch):
    client = setup(tmp_path, monkeypatch)
    resp = client.get("/agent/default/tasks")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["current_task"] == "c"
    assert data["tasks"] == [{"task": "t1", "agent": "default"}]
