from agent.services.goal_config_resolver_service import (
    ALLOWED_GOAL_CONFIG_KEYS,
    get_goal_config_resolver_service,
)


def test_allowed_goal_config_keys_is_public_frozenset():
    assert isinstance(ALLOWED_GOAL_CONFIG_KEYS, frozenset)
    assert "default_provider" in ALLOWED_GOAL_CONFIG_KEYS
    assert "default_model" in ALLOWED_GOAL_CONFIG_KEYS
    assert "llm_config" in ALLOWED_GOAL_CONFIG_KEYS


def test_merge_precedence_is_deterministic():
    resolver = get_goal_config_resolver_service()
    system = {"default_provider": "ollama", "default_model": "base", "llm_config": {"base_url": "http://a"}}
    result1 = resolver.resolve(
        system_config=system,
        profile_id="ananta_ollama_local",
        goal_overrides={"default_model": "goal-model"},
        task_overrides={"default_model": "task-model"},
    )
    result2 = resolver.resolve(
        system_config=system,
        profile_id="ananta_ollama_local",
        goal_overrides={"default_model": "goal-model"},
        task_overrides={"default_model": "task-model"},
    )
    assert result1.config_snapshot == result2.config_snapshot
    assert result1.checksum == result2.checksum
    assert result1.config_snapshot["config"]["default_model"] == "task-model"


def test_unknown_keys_are_excluded_and_reported():
    resolver = get_goal_config_resolver_service()
    result = resolver.resolve(
        system_config={"default_provider": "ollama"},
        profile_id=None,
        goal_overrides={"unknown_key": "x", "default_model": "m1"},
        task_overrides={"another_bad_key": "y"},
    )
    cfg = result.config_snapshot["config"]
    assert "unknown_key" not in cfg
    assert "another_bad_key" not in cfg
    assert cfg["default_model"] == "m1"
    assert "unknown_key" in result.unknown_keys
    assert "another_bad_key" in result.unknown_keys


def test_unknown_keys_empty_when_all_keys_valid():
    resolver = get_goal_config_resolver_service()
    result = resolver.resolve(
        system_config={"default_provider": "ollama"},
        profile_id=None,
        goal_overrides={"default_model": "m1"},
    )
    assert result.unknown_keys == ()


def test_checksum_computed_over_redacted_snapshot():
    resolver = get_goal_config_resolver_service()
    # Two resolutions with different secret values must produce the same checksum
    # because the checksum covers only the redacted form.
    result_a = resolver.resolve(
        system_config={"llm_config": {"api_key": "secret-A", "base_url": "http://x"}},
        profile_id=None,
    )
    result_b = resolver.resolve(
        system_config={"llm_config": {"api_key": "secret-B", "base_url": "http://x"}},
        profile_id=None,
    )
    assert result_a.checksum == result_b.checksum
    # Non-secret change must change the checksum.
    result_c = resolver.resolve(
        system_config={"llm_config": {"api_key": "secret-A", "base_url": "http://y"}},
        profile_id=None,
    )
    assert result_a.checksum != result_c.checksum


def test_redaction_masks_secret_fields():
    resolver = get_goal_config_resolver_service()
    result = resolver.resolve(
        system_config={"llm_config": {"api_key": "secret", "token": "abc", "base_url": "http://x"}},
        profile_id=None,
    )
    llm = result.config_snapshot["config"]["llm_config"]
    assert llm["api_key"] == "***REDACTED***"
    assert llm["token"] == "***REDACTED***"
    assert result.redaction_summary["redacted_fields"] >= 2


def test_redaction_covers_extended_markers():
    resolver = get_goal_config_resolver_service()
    result = resolver.resolve(
        system_config={
            "llm_config": {
                "bearer_token": "tok",
                "authorization": "Bearer xyz",
                "credential": "cred123",
                "base_url": "http://x",
            }
        },
        profile_id=None,
    )
    llm = result.config_snapshot["config"]["llm_config"]
    assert llm["bearer_token"] == "***REDACTED***"
    assert llm["authorization"] == "***REDACTED***"
    assert llm["credential"] == "***REDACTED***"
    assert llm["base_url"] == "http://x"
    assert result.redaction_summary["redacted_fields"] >= 3


def test_redaction_walks_nested_lists():
    resolver = get_goal_config_resolver_service()
    result = resolver.resolve(
        system_config={
            "llm_config": {
                "providers": [
                    {"name": "openai", "api_key": "sk-123"},
                    {"name": "local", "base_url": "http://local"},
                ]
            }
        },
        profile_id=None,
    )
    providers = result.config_snapshot["config"]["llm_config"]["providers"]
    assert providers[0]["api_key"] == "***REDACTED***"
    assert providers[0]["name"] == "openai"
    assert providers[1]["base_url"] == "http://local"
