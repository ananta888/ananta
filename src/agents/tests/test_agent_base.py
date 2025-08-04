import json
from pathlib import Path

import pytest

from src.agents import Agent, load_agents


def _write_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_load_agents(tmp_path: Path):
    cfg_dir = tmp_path / "config/agents"
    data = {
        "name": "demo",
        "provider": "openai",
        "model": "gpt-3.5",
        "prompt_template": "Hello {name}",
    }
    _write_config(cfg_dir / "demo.json", data)

    agents = load_agents(cfg_dir)

    assert "demo" in agents
    agent = agents["demo"]
    assert agent.name == "demo"
    assert agent.provider == "openai"
    assert agent.model == "gpt-3.5"
    assert agent.prompt_template == "Hello {name}"
    assert Path(agent.config_path).samefile(cfg_dir / "demo.json")


def test_missing_required_fields(tmp_path: Path):
    cfg_path = tmp_path / "missing.json"
    # Missing 'provider' and 'model'
    data = {
        "name": "broken",
        "prompt_template": "template",
    }
    _write_config(cfg_path, data)

    with pytest.raises(ValueError):
        Agent.from_file(cfg_path)
