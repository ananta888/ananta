import yaml
import pytest
import controller.controller as cc


@pytest.fixture(autouse=True)
def controller_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("active_agent: default\nagents: {}\napi_endpoints: []\nprompt_templates: {}\n")
    tasks = tmp_path / "tasks.json"
    tasks.write_text("[]")
    monkeypatch.setattr(cc, "CONFIG_FILE", str(cfg))
    monkeypatch.setattr(cc, "TASKS_FILE", str(tasks))
    cc.config_manager = cc.ConfigManager(cfg)
    cc.task_store = cc.TaskStore(tasks)
    cc.config_provider = cc.FileConfig(cc.read_config, cc.write_config)
    yield

