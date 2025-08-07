import json
import yaml
import controller.controller as cc


def setup(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("active_agent: default\nagents:\n  default: {}\napi_endpoints: []\nprompt_templates: {}\n")
    tasks_file = tmp_path / "tasks.json"
    monkeypatch.setattr(cc, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cc, "CONFIG_FILE", str(cfg_file))
    monkeypatch.setattr(cc, "TASKS_FILE", str(tasks_file))
    cc.config_manager = cc.ConfigManager(cfg_file)
    cc.task_store = cc.TaskStore(tasks_file)
    return cc.app.test_client(), cfg_file, tasks_file


def test_add_task_and_next(tmp_path, monkeypatch):
    client, cfg_file, tasks_file = setup(tmp_path, monkeypatch)
    resp = client.post("/agent/add_task", json={"task": "t1"})
    assert resp.status_code == 201
    resp = client.get("/tasks/next")
    assert resp.get_json()["task"] == "t1"
    saved_tasks = json.loads(tasks_file.read_text())
    assert saved_tasks == []
    cfg = yaml.safe_load(cfg_file.read_text())
    assert cfg["agents"]["default"]["current_task"] == "t1"
