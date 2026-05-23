from __future__ import annotations

from worker.core.hermes_default_config import (
    OPENROUTER_BASE_URL,
    OPENROUTER_PRIMARY_MODEL,
    build_hermes_adapter_config_from_agent_config,
    build_openrouter_hermes_adapter_config,
)


def test_openrouter_hermes_default_config_is_enabled_and_not_free() -> None:
    cfg = build_openrouter_hermes_adapter_config()

    assert cfg.enabled is True
    assert cfg.feature_flag_enabled is True
    assert cfg.base_url == OPENROUTER_BASE_URL
    assert cfg.api_key_env == "OPENROUTER_API_KEY"
    assert cfg.default_model == OPENROUTER_PRIMARY_MODEL
    assert cfg.cloud_allowed is True
    assert cfg.max_retries == 1
    assert cfg.strict_json_required is True
    assert ":free" in cfg.blocked_models
    assert not cfg.default_model.endswith(":free")


def test_hermes_worker_type_auto_enables_adapter_defaults() -> None:
    cfg = build_hermes_adapter_config_from_agent_config({"worker": {"type": "hermes"}})

    assert cfg.enabled is True
    assert cfg.feature_flag_enabled is True
    assert cfg.default_model == OPENROUTER_PRIMARY_MODEL
    assert cfg.task_kind_models["review"] == "deepseek/deepseek-v4-flash"
    assert cfg.task_kind_models["summarize"] == "google/gemini-2.5-flash-lite"


def test_explicit_hermes_worker_adapter_overrides_defaults() -> None:
    cfg = build_hermes_adapter_config_from_agent_config(
        {
            "worker": {"type": "hermes"},
            "hermes_worker_adapter": {
                "default_model": "custom/model",
                "base_url": "https://example.test/v1",
                "cloud_allowed": False,
            },
        }
    )

    assert cfg.enabled is True
    assert cfg.feature_flag_enabled is True
    assert cfg.default_model == "custom/model"
    assert cfg.base_url == "https://example.test/v1"
    assert cfg.cloud_allowed is False
