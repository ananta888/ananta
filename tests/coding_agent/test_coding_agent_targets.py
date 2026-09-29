from agent.cli_backends.coding_agent_targets import resolve_aider_inference_target


def test_aider_resolves_configured_local_openai_target_without_code_changes() -> None:
    target = resolve_aider_inference_target(
        agent_config={
            "default_provider": "openai",
            "default_model": "cloud-default",
            "aider_cli": {"target_provider": "local_coder", "model": "qwen3-coder"},
            "local_openai_backends": [
                {
                    "id": "local_coder",
                    "base_url": "http://127.0.0.1:9000/v1/chat/completions",
                    "models": ["qwen3-coder"],
                }
            ],
        },
        provider_urls={},
        environment={},
    )

    assert target.client_id == "aider"
    assert target.provider_id == "local_coder"
    assert target.model == "qwen3-coder"
    assert target.cli_model == "openai/qwen3-coder"
    assert target.base_url == "http://127.0.0.1:9000/v1"
    assert target.target_kind == "local_openai"
    assert target.api_key_source == "local_dummy"
    assert target.process_environment() == {
        "OPENAI_API_BASE": "http://127.0.0.1:9000/v1",
        "OPENAI_BASE_URL": "http://127.0.0.1:9000/v1",
        "OPENAI_API_KEY": "sk-no-key-needed",
    }


def test_explicit_hosted_model_prefix_is_separate_from_aider_client() -> None:
    target = resolve_aider_inference_target(
        model="openrouter/qwen/qwen3-coder:free",
        agent_config={"default_provider": "lmstudio"},
        provider_urls={"openrouter": "https://openrouter.ai/api/v1"},
        environment={"OPENROUTER_API_KEY": "not-projected-as-openai-key"},
    )

    assert target.client_id == "aider"
    assert target.provider_id == "openrouter"
    assert target.model == "qwen/qwen3-coder:free"
    assert target.cli_model == "openai/qwen/qwen3-coder:free"
    assert target.target_kind == "remote_openai_compatible"
    assert "api_key" not in target.public_metadata()


def test_explicit_aider_target_policy_overrides_model_prefix() -> None:
    target = resolve_aider_inference_target(
        model="openrouter/cloud-model",
        agent_config={
            "default_provider": "openai",
            "aider_cli": {"target_provider": "lmstudio", "model": "local-model"},
        },
        provider_urls={"lmstudio": "http://127.0.0.1:1234/v1"},
        environment={},
    )

    assert target.provider_id == "lmstudio"
    assert target.model == "openrouter/cloud-model"
    assert target.cli_model == "openai/openrouter/cloud-model"
