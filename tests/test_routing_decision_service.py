from agent.services.routing_decision_service import get_routing_decision_service


def test_routing_decision_chain_explains_benchmark_selection():
    service = get_routing_decision_service()

    chain = service.build_decision_chain(
        cfg={},
        task_kind="coding",
        requested={},
        effective={"provider": "vllm_local", "model": "qwen2.5-coder"},
        sources={"provider_source": "agent_config.default_provider", "model_source": "agent_config.default_model"},
        recommendation={
            "provider": "vllm_local",
            "model": "qwen2.5-coder",
            "selection_source": "benchmarks_available_top_ranked",
        },
    )

    assert chain["policy_version"] == "routing-decision-v1"
    assert chain["task_kind"] == "coding"
    assert chain["steps"][0]["step"] == "task_benchmark"
    assert chain["steps"][0]["decision"] == "selected"
    assert chain["effective"]["provider"] == "vllm_local"
    assert chain["fallback_policy"]["allow_remote_hubs"] is True


def test_routing_fallback_policy_normalizes_order_and_unavailable_action():
    service = get_routing_decision_service()

    policy = service.normalize_fallback_policy(
        {
            "allow_remote_hubs": False,
            "fallback_order": ["remote_hub", "unknown", "configured_default", "remote_hub"],
            "unavailable_action": "block",
        }
    )

    assert policy["allow_remote_hubs"] is False
    assert policy["fallback_order"] == ["remote_hub", "configured_default"]
    assert policy["unavailable_action"] == "block"


def test_provider_catalog_decision_blocks_remote_hub_when_policy_disallows_it():
    service = get_routing_decision_service()

    decision = service.provider_catalog_decision(
        cfg={"routing_fallback_policy": {"allow_remote_hubs": False}},
        provider={
            "provider": "remote",
            "available": True,
            "provider_type": "remote_ananta",
            "remote_hub": True,
            "capabilities": {"dynamic_models": True},
        },
        task_kind="coding",
    )

    assert decision["available_for_routing"] is False
    assert decision["reason"] == "remote_hub_fallback_disabled"


def test_routing_decision_chain_includes_context_policy_scope_step():
    service = get_routing_decision_service()

    chain = service.build_decision_chain(
        cfg={},
        task_kind="research",
        requested={},
        effective={"provider": "openai", "model": "gpt-4o", "llm_scope": "external_cloud_allowed"},
        sources={"provider_source": "agent_config.default_provider", "model_source": "agent_config.default_model"},
    )

    assert any(step.get("step") == "context_policy_scope" for step in list(chain.get("steps") or []))
