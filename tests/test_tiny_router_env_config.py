from agent.config_defaults import apply_env_config_overrides, build_default_agent_config


def test_tiny_router_shadow_mode_can_be_deployed_via_environment(monkeypatch):
    monkeypatch.setenv("ANANTA_TINY_ROUTER_MODE", "shadow")
    monkeypatch.setenv(
        "ANANTA_TINY_ROUTER_PROFILES", "needle-2-45m, lfm2.5-2.6b-agentic",
    )
    config = build_default_agent_config()

    apply_env_config_overrides(config)

    tiny = config["ananta_worker_tool_loop"]["tiny_router"]
    assert tiny["mode"] == "shadow"
    assert tiny["profile_order"] == ["needle-2-45m", "lfm2.5-2.6b-agentic"]
