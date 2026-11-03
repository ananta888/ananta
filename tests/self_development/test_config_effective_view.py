"""Effective configuration view: layer attribution and secret redaction."""

import json
from types import SimpleNamespace

import agent.config_defaults_env as config_defaults_env
import agent.repository as repository
import agent.services.config_effective_view as view


def test_redact_masks_secret_keys_recursively():
    payload = {
        "api_key": "tp-secret",
        "api_key_profile": "mimo",
        "name": "mimo",
        "nested": {"auth_token": "t", "base_url": "http://x"},
        "list": [{"api_key": "k"}],
    }
    redacted = view._redact(payload)
    assert redacted["api_key"] == "***"
    assert redacted["api_key_profile"] == "mimo"
    assert redacted["name"] == "mimo"
    assert redacted["nested"] == {"auth_token": "***", "base_url": "http://x"}
    assert redacted["list"] == [{"api_key": "***"}]


def test_redact_masks_by_key_hint_for_scalars():
    assert view._redact("plain", "OPENAI_API_KEY") == "***"
    assert view._redact("plain", "default_model") == "plain"


class _FakeRepo:
    def __init__(self, rows):
        self.rows = rows

    def get_all(self):
        return self.rows


def test_view_attributes_db_and_env_layers(monkeypatch):
    rows = [
        SimpleNamespace(key="default_model", value_json=json.dumps("db-model")),
        SimpleNamespace(key="only_in_db", value_json=json.dumps({"api_key": "s"})),
    ]
    monkeypatch.setattr(repository, "config_repo", _FakeRepo(rows), raising=False)
    monkeypatch.setattr(view, "_db_values", lambda: {"default_model": "db-model", "only_in_db": {"api_key": "s"}})

    def env_override(cfg):
        cfg["default_model"] = "env-model"

    monkeypatch.setattr(config_defaults_env, "apply_env_config_overrides", env_override)

    result = view.build_effective_config_view()
    entries = result["entries"]
    assert entries["default_model"] == {"source": "env", "value": "env-model"}
    assert entries["only_in_db"] == {"source": "db", "value": {"api_key": "***"}}
    default_keys = [key for key, entry in entries.items() if entry["source"] == "default"]
    assert default_keys, "pure defaults must be attributed as default"
