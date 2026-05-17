from agent.services.goal_config_resolver_service import get_goal_config_resolver_service


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


def test_unknown_keys_in_overrides_are_ignored():
    resolver = get_goal_config_resolver_service()
    result = resolver.resolve(
        system_config={"default_provider": "ollama"},
        profile_id=None,
        goal_overrides={"unknown_key": "x", "default_model": "m1"},
    )
    cfg = result.config_snapshot["config"]
    assert "unknown_key" not in cfg
    assert cfg["default_model"] == "m1"


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
