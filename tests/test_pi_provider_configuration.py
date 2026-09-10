"""Explicit documented provider parameters, independent of SDK hostname guesses."""

import json

import pytest

from agent.cli_backends.pi_configuration import isolated_pi_configuration
from tests.test_pi_coding_agent_provider import target


@pytest.mark.parametrize("provider_id,base_url,model", [
    ("ollama", "http://ollama:11434/v1", "hub-selected:tag"),
    ("lmstudio", "http://lmstudio:1234/v1", "hub-selected-model"),
    ("openrouter", "https://openrouter.ai/api/v1", "publisher/hub-selected-model"),
])
def test_configuration_explicitly_uses_documented_token_field_and_no_implicit_features(
    tmp_path, provider_id, base_url, model,
):
    selected = target(provider_id=provider_id, base_url=base_url, model=model, cli_model=model)
    with isolated_pi_configuration(selected, project=tmp_path / "project", runtime_root=tmp_path, max_tokens=37) as run:
        config = json.loads((run.config_directory / "models.json").read_text())["providers"]["ananta"]
        assert config["baseUrl"] == base_url and config["api"] == "openai-completions"
        assert config["models"] == [{"id": model, "contextWindow": 8192, "maxTokens": 37}]
        compat = config["compat"]
        assert compat["maxTokensField"] == "max_tokens"
        assert compat["supportsStore"] is False and compat["supportsDeveloperRole"] is False
        assert compat["supportsReasoningEffort"] is False and compat["supportsUsageInStreaming"] is False
        assert compat["supportsFinishReason"] is True
        if provider_id == "openrouter":
            assert compat["openRouterRouting"] == {"allow_fallbacks": False, "require_parameters": True}
        else:
            assert "openRouterRouting" not in compat
        assert selected.api_key not in json.dumps(config)
