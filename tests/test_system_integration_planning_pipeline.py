from agent.llm_benchmarks import record_benchmark_sample, recommend_profiles_for_context
from agent.services.context_file_reader_service import ContextFileReaderService, FileReadPolicy
from agent.services.embedding_provider_config_service import EmbeddingProviderConfigService
from agent.services.model_profile_loader import ModelProfile
from agent.services.model_profile_resolver import ModelProfileResolver, RoutingContext, SecurityPolicyChecker
from agent.services.routing_decision_service import get_routing_decision_service
from agent.services.worker_contract_service import get_worker_contract_service


def test_end_to_end_planning_pipeline_without_network(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    source = workspace / "app.py"
    source.write_text("def hello():\n    return 'hallo'\n", encoding="utf-8")

    embedding_cfg = EmbeddingProviderConfigService().resolve("worker_retrieval")
    assert embedding_cfg.provider == "local_hash"
    assert embedding_cfg.external_calls_allowed is False

    profile = ModelProfile(
        profile_id="local-planner",
        provider_id="ollama",
        model="qwen2.5-coder:7b",
        model_role="planner",
        local=True,
        cloud=False,
        cloud_allowed=False,
        block_secret_context=False,
        supports_json=True,
    )
    resolver = ModelProfileResolver(profiles=[profile])
    routing = resolver.resolve(RoutingContext(model_role="planner", task_kind="coding", requires_json=True))
    assert routing.ok

    reader = ContextFileReaderService(FileReadPolicy(workspace_root=workspace))
    context_files = [item.as_context_file_dict() for item in reader.read_files(["app.py"])]
    handoff = get_worker_contract_service().build_worker_context_handoff_v3(
        question="Bitte app.py analysieren",
        candidate_files=[{"path": "app.py", "score": 1.0, "reason": "query_match"}],
        context_files=context_files,
    )
    decision_chain = get_routing_decision_service().build_decision_chain(
        cfg={},
        task_kind="coding",
        requested={},
        effective={
            "provider": routing.profile.provider_id,
            "model": routing.profile.model,
            "model_profile_id": routing.profile.profile_id,
        },
        sources={"provider_source": routing.final_source, "model_source": routing.final_source},
    )

    assert handoff["schema"] == "worker_context_handoff.v3"
    assert handoff["context_files"][0]["path"] == "app.py"
    assert decision_chain["steps"][0]["step"] == "configured_default"
    assert decision_chain["effective"]["model_profile_id"] == "local-planner"


def test_simple_tool_route_can_skip_rag_with_route_source():
    chain = get_routing_decision_service().build_decision_chain(
        cfg={},
        task_kind="list_root_files",
        requested={"tool": "list_root_files"},
        effective={"route_source": "direct_tool", "provider": None, "model": None},
        sources={"provider_source": "none", "model_source": "none"},
    )

    assert chain["effective"]["route_source"] == "direct_tool"
    assert chain["steps"][0]["step"] == "request_override"


def test_cloud_provider_blocked_when_secret_context_would_be_sent(tmp_path):
    cloud = ModelProfile(
        profile_id="cloud-planner",
        provider_id="openai",
        model="gpt-4o",
        model_role="planner",
        local=False,
        cloud=True,
        cloud_allowed=True,
        block_secret_context=True,
    )
    local = ModelProfile(
        profile_id="local-planner",
        provider_id="ollama",
        model="qwen2.5-coder:7b",
        model_role="planner",
        local=True,
        cloud=False,
        cloud_allowed=False,
        block_secret_context=False,
    )
    for _ in range(3):
        record_benchmark_sample(
            data_dir=str(tmp_path),
            agent_cfg={},
            provider="openai",
            model="gpt-4o",
            profile_id="cloud-planner",
            task_kind="coding",
            success=True,
            quality_gate_passed=True,
            latency_ms=10,
            tokens_total=100,
        )
    ranked = recommend_profiles_for_context(
        data_dir=str(tmp_path),
        task_kind="coding",
        allowed_profile_ids=["cloud-planner", "local-planner"],
        min_samples=3,
    )
    resolver = ModelProfileResolver(
        profiles=[cloud, local],
        security_policy=SecurityPolicyChecker(block_cloud_with_secrets=True),
        benchmark_profile_order=[row["profile_id"] for row in ranked],
    )

    routing = resolver.resolve(RoutingContext(model_role="planner", task_kind="coding", context_text="password=supersecret"))

    assert routing.ok
    assert routing.profile.profile_id == "local-planner"
    assert any(profile_id == "cloud-planner" for profile_id, _reason in routing.blocked_candidates)
