from tests.test_dashboard_manager import DummyConfig, make_request, DEFAULT_AGENT
from src.dashboard import DashboardManager


def test_handle_post_updates_config():
    cfg = {"agents": {}, "pipeline_order": [], "tasks": [], "api_endpoints": [], "prompt_templates": {}}
    dm = DashboardManager(DummyConfig(cfg), DEFAULT_AGENT, ["ollama"])
    form = {
        "new_agent": "agent1",
        "set_active": "agent1",
        "add_task": "1",
        "task_text": "task1",
        "task_agent": "agent1",
        "agent": "agent1",
        "models": "m1,m2",
        "api_endpoints_form": "1",
        "add_endpoint": "1",
        "new_endpoint_type": "lmstudio",
        "new_endpoint_url": "http://llm",
        "new_endpoint_models": "x",
    }
    dm.handle_post(make_request(form))
    updated = dm.config.read()
    assert updated["active_agent"] == "agent1"
    assert updated["tasks"][0]["task"] == "task1"
    assert updated["agents"]["agent1"]["models"] == ["m1", "m2"]
    assert updated["api_endpoints"][0] == {"type": "lmstudio", "url": "http://llm", "models": ["x"]}
