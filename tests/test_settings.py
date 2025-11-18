import os
from pathlib import Path
import json
import builtins

import pytest

from src.config.settings import Settings, load_settings


def _defaults_path() -> str:
    here = Path(__file__).resolve().parent
    # src/config/defaults.json relative to repo root
    return str(here.parent / "src" / "config" / "defaults.json")


def test_settings_load_defaults(monkeypatch):
    # Ensure no environment overrides are present
    for k in list(os.environ.keys()):
        if k.startswith("AI_AGENT_") or k in {"CONTROLLER_URL", "OPENAI_API_KEY", "AGENT_NAME", "AGENT_STARTUP_DELAY"}:
            monkeypatch.delenv(k, raising=False)

    s = Settings.from_sources(defaults_path=_defaults_path(), env_json_path=None, env={})
    assert s.controller_url == "http://controller:8081"
    assert s.agent_name == "Architect"
    assert s.agent_startup_delay == 3
    assert s.log_level.upper() == "INFO"
    assert s.http_timeout_get == 10.0
    assert s.retry.total == 3


def test_settings_env_json_overrides(tmp_path, monkeypatch):
    # Create an env.json that overrides some defaults
    env_json = tmp_path / "env.json"
    env_payload = {
        "controller_url": "http://override:9999",
        "http": {"timeout_get": 2.5, "timeout_post": 7},
        "retry": {"total": 5, "backoff_factor": 0.2, "status_forcelist": [500, 502]},
        "agent_name": "OverAgent"
    }
    env_json.write_text(json.dumps(env_payload), encoding="utf-8")

    s = Settings.from_sources(defaults_path=_defaults_path(), env_json_path=str(env_json), env={})
    assert s.controller_url == "http://override:9999"
    assert s.http_timeout_get == 2.5
    assert s.http_timeout_post == 7.0
    assert s.retry.total == 5
    assert s.retry.backoff_factor == 0.2
    assert s.retry.status_forcelist == [500, 502]
    assert s.agent_name == "OverAgent"


def test_settings_env_vars_override_env_json(tmp_path, monkeypatch):
    # env.json sets value A, but ENV should win with value B
    env_json = tmp_path / "env.json"
    env_json.write_text(json.dumps({"controller_url": "http://from-env-json:8080"}), encoding="utf-8")

    env = {
        "AI_AGENT_CONTROLLER_URL": "http://from-env:7070",
        "AI_AGENT_NAME": "EnvAgent",
        "AI_AGENT_STARTUP_DELAY": "10",
        "AI_AGENT_LOG_JSON": "true",
        "AI_AGENT_LOG_LEVEL": "debug",
    }

    s = Settings.from_sources(defaults_path=_defaults_path(), env_json_path=str(env_json), env=env)
    assert s.controller_url == "http://from-env:7070"
    assert s.agent_name == "EnvAgent"
    assert s.agent_startup_delay == 10
    assert s.log_json is True
    assert s.log_level.upper() == "DEBUG"


def test_settings_validation_invalid_url():
    env = {"AI_AGENT_CONTROLLER_URL": "not-a-url"}
    with pytest.raises(ValueError) as exc:
        Settings.from_sources(defaults_path=_defaults_path(), env_json_path=None, env=env)
    assert "Ungültige URL" in str(exc.value)
