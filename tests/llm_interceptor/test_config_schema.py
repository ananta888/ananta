from __future__ import annotations

import json

import pytest

from agent.services.llm_interceptor.config_schema import LlmInterceptorConfig, load_llm_interceptor_config


def _valid_config() -> dict:
    return {
        "listen": {"host": "127.0.0.1", "port": 8787, "prefix": "/v1"},
        "upstreams": [
            {
                "id": "local",
                "type": "openai_compatible",
                "base_url": "http://127.0.0.1:1234/v1",
                "api_key_env": None,
                "trust_level": "local",
                "allowed_models": ["intercepted-coder"],
            }
        ],
        "routing": {
            "default_upstream": "local",
            "default_model": "intercepted-coder",
            "rules": [],
        },
        "policy": {"max_context_chars_default": 120000},
        "redaction": {"enabled": True, "patterns": ["token"]},
        "response_validation": {"structured_json_repair_attempts": 1},
    }


def test_schema_accepts_valid_config():
    cfg = LlmInterceptorConfig.model_validate(_valid_config())
    assert cfg.listen.port == 8787
    assert cfg.routing.default_upstream == "local"


def test_schema_rejects_unknown_default_upstream():
    raw = _valid_config()
    raw["routing"]["default_upstream"] = "missing"
    with pytest.raises(Exception):
        LlmInterceptorConfig.model_validate(raw)


def test_schema_rejects_duplicate_upstream_ids():
    raw = _valid_config()
    raw["upstreams"].append(dict(raw["upstreams"][0]))
    with pytest.raises(Exception):
        LlmInterceptorConfig.model_validate(raw)


def test_load_config_fails_on_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{bad", encoding="utf-8")
    with pytest.raises(ValueError):
        load_llm_interceptor_config(path)


def test_load_config_success(tmp_path):
    path = tmp_path / "ok.json"
    path.write_text(json.dumps(_valid_config()), encoding="utf-8")
    cfg = load_llm_interceptor_config(path)
    assert cfg.listen.prefix == "/v1"

