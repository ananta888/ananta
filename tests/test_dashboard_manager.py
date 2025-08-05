from typing import Dict
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request
from src.dashboard import DashboardManager


class DummyConfig:
    def __init__(self, cfg: Dict):
        self.cfg = cfg

    def read(self) -> Dict:
        return self.cfg

    def write(self, config: Dict) -> None:
        self.cfg = config


def make_request(form: Dict[str, str]) -> Request:
    builder = EnvironBuilder(method="POST", data=form)
    return Request(builder.get_environ())


DEFAULT_AGENT = {
    "model": "m",
    "provider": "p",
    "template": "",
    "max_summary_length": 0,
    "step_delay": 0,
    "auto_restart": False,
    "allow_commands": True,
    "controller_active": True,
    "prompt": "",
    "tasks": [],
}


def test_reorder_and_tasks():
    cfg = {"agents": {}, "pipeline_order": ["a", "b"], "tasks": [{"task": "x"}, {"task": "y"}]}
    dm = DashboardManager(DummyConfig(cfg), DEFAULT_AGENT, ["ollama"])

    dm._reorder_pipeline(cfg, make_request({"move_agent": "b", "direction": "up"}))
    assert cfg["pipeline_order"] == ["b", "a"]

    dm._manage_tasks(cfg, make_request({"task_action": "move_up", "task_idx": "1"}))
    assert cfg["tasks"][0]["task"] == "y"

    dm._manage_tasks(cfg, make_request({"add_task": "1", "task_text": "z"}))
    assert any(t["task"] == "z" for t in cfg["tasks"])


def test_handle_new_agent_and_endpoints():
    cfg = {"agents": {}, "pipeline_order": [], "api_endpoints": [{"type": "ollama", "url": "old"}]}
    dm = DashboardManager(DummyConfig(cfg), DEFAULT_AGENT, ["ollama"])

    dm._handle_new_agent(cfg, make_request({"new_agent": "agent1"}))
    assert "agent1" in cfg["agents"]
    assert cfg["pipeline_order"] == ["agent1"]

    dm._update_endpoints(cfg, make_request({"api_endpoints_form": "1", "endpoint_url_0": "new"}))
    assert cfg["api_endpoints"][0]["url"] == "new"
