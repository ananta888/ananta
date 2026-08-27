from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import requests

from agent.services.model_invocation_service import ModelInvocationService
from agent.services.propose_runtime_policy import (
    _calibrated_timeout_from_benchmarks,
    resolve_propose_llm_timeout_seconds,
)


def test_normalize_openai_tools_converts_flat_registry_shape() -> None:
    tools = [
        {
            "name": "write_file",
            "description": "write a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        }
    ]
    normalized = ModelInvocationService._normalize_openai_tools(tools)
    assert len(normalized) == 1
    item = normalized[0]
    assert item["type"] == "function"
    assert item["function"]["name"] == "write_file"
    assert item["function"]["description"] == "write a file"
    assert item["function"]["parameters"]["type"] == "object"


def test_make_chat_call_sends_normalized_tools_payload(monkeypatch) -> None:
    import agent.services.model_invocation_service as service_module

    captured: dict = {}
    monkeypatch.setattr(service_module, "_PROFILE_RESOLVER_CACHE", None)
    monkeypatch.delenv("MODEL_PROFILES_PATH", raising=False)
    monkeypatch.delenv("MODEL_ROUTING_PATH", raising=False)
    monkeypatch.delenv("ANANTA_MODEL_ROUTING_PATH", raising=False)

    def _fake_post(  # noqa: ANN001
        url, json, headers, timeout, allow_redirects
    ):
        assert allow_redirects is False
        captured["url"] = url
        captured["body"] = dict(json or {})
        captured["timeout"] = timeout
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{"message": {"content": "ok", "tool_calls": []}, "finish_reason": "stop"}],
                "usage": {},
                "model": "local-model",
            },
        )

    monkeypatch.setattr("agent.services.model_invocation_service.requests.post", _fake_post)
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_settings",
        classmethod(
            lambda cls: SimpleNamespace(  # noqa: ARG005
                default_provider="lmstudio",
                default_model="auto",
                lmstudio_url="http://localhost:1234/v1",
                ollama_url="http://localhost:11434/api/generate",
                openai_url="https://api.openai.com/v1",
                openai_api_key=None,
                mock_url="http://mock",
                llm_invoke_timeout_seconds=120,
            )
        ),
    )

    ModelInvocationService.invoke_with_tools(
        prompt="hello",
        tools=[{"name": "file_read", "description": "d", "parameters": {"type": "object", "properties": {}}}],
        timeout=333,
    )

    assert captured["timeout"] == 333
    assert captured["body"]["tools"][0]["type"] == "function"
    assert captured["body"]["tools"][0]["function"]["name"] == "file_read"


def test_provider_invocation_cancellation_fences_network_and_late_response(monkeypatch) -> None:
    from agent.services.model_invocation_service import LLMUnavailableError

    attempt = {
        "provider": "lmstudio",
        "url": "http://localhost:1234/v1/chat/completions",
        "api_key": None,
        "model": "local-model",
        "timeout": 5,
        "profile": None,
    }
    monkeypatch.setattr(
        "agent.services.model_invocation_service.requests.post",
        lambda **_kwargs: pytest.fail("pre-cancelled invocation must not send"),
    )
    monkeypatch.setattr(
        ModelInvocationService,
        "_current_invocation_cancelled",
        staticmethod(lambda: True),
    )

    with pytest.raises(LLMUnavailableError) as pre_cancelled:
        ModelInvocationService._make_single_chat_call(
            [{"role": "user", "content": "secret"}],
            tools=None,
            response_format=None,
            attempt=attempt,
            resolution_info={},
        )
    assert pre_cancelled.value.terminal_reason == "cancelled"

    class Middleware:
        def __init__(self):
            self.failures = []

        def prepare(self, **values):
            return SimpleNamespace(payload=values["payload"], cached_response=None)

        def fail(self, _prepared, **values):
            self.failures.append(values["reason_code"])

    middleware = Middleware()
    cancellation_checks = iter((False, True))
    monkeypatch.setattr(
        ModelInvocationService,
        "_current_invocation_cancelled",
        staticmethod(lambda: next(cancellation_checks)),
    )
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_provider_middleware",
        classmethod(lambda _cls: middleware),
    )
    monkeypatch.setattr(
        "agent.services.model_invocation_service.requests.post",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=200),
    )

    with pytest.raises(LLMUnavailableError) as late_cancelled:
        ModelInvocationService._make_single_chat_call(
            [{"role": "user", "content": "secret"}],
            tools=None,
            response_format=None,
            attempt=attempt,
            resolution_info={},
        )
    assert late_cancelled.value.terminal_reason == "cancelled"
    assert middleware.failures == ["cancelled"]


def test_explicit_missing_profiles_path_fails_closed_without_legacy_call(
    monkeypatch,
    tmp_path,
) -> None:
    import agent.services.model_invocation_service as svc_mod
    from agent.services.model_invocation_service import LLMUnavailableError

    monkeypatch.setattr(svc_mod, "_PROFILE_RESOLVER_CACHE", None)
    monkeypatch.setenv(
        "MODEL_PROFILES_PATH",
        str(tmp_path / "missing.model_profiles.yaml"),
    )
    monkeypatch.delenv("MODEL_ROUTING_PATH", raising=False)
    monkeypatch.delenv("ANANTA_MODEL_ROUTING_PATH", raising=False)
    monkeypatch.setattr(
        "agent.services.model_invocation_service.requests.post",
        lambda *args, **kwargs: pytest.fail("legacy provider must not be called"),
    )

    with pytest.raises(LLMUnavailableError) as raised:
        ModelInvocationService.invoke_result("hello")

    assert raised.value.terminal_reason == "policy_blocked"
    assert raised.value.fallback_decisions == [
        {
            "reason": "configured_model_routing_unavailable",
            "previous_profile_id": None,
            "next_profile_id": None,
            "trigger": "policy_blocked",
            "terminal": True,
        }
    ]


def test_explicit_invalid_routing_file_fails_closed_without_legacy_call(
    monkeypatch,
    tmp_path,
) -> None:
    import agent.services.model_invocation_service as svc_mod
    from agent.services.model_invocation_service import LLMUnavailableError

    profiles_path = tmp_path / "profiles.json"
    profiles_path.write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "profile_id": "local",
                        "provider_id": "ollama",
                        "model": "phi4-mini",
                        "local": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    routing_path = tmp_path / "routing.json"
    routing_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "routing_rules": "invalid-not-an-array",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(svc_mod, "_PROFILE_RESOLVER_CACHE", None)
    monkeypatch.setenv("MODEL_PROFILES_PATH", str(profiles_path))
    monkeypatch.setenv("MODEL_ROUTING_PATH", str(routing_path))
    monkeypatch.delenv("ANANTA_MODEL_ROUTING_PATH", raising=False)
    monkeypatch.setattr(
        "agent.services.model_invocation_service.requests.post",
        lambda *args, **kwargs: pytest.fail("legacy provider must not be called"),
    )

    with pytest.raises(LLMUnavailableError) as raised:
        ModelInvocationService.invoke_result("hello")

    assert raised.value.terminal_reason == "policy_blocked"
    assert raised.value.fallback_decisions[0]["trigger"] == "policy_blocked"


@pytest.mark.parametrize(
    "failure_kind",
    ["invalid_profiles", "missing_routing"],
)
def test_all_explicit_config_load_failures_are_distinct_from_not_configured(
    monkeypatch,
    tmp_path,
    failure_kind,
) -> None:
    import agent.services.model_invocation_service as svc_mod
    from agent.services.model_invocation_service import (
        ModelRoutingConfigurationError,
    )

    profiles_path = tmp_path / "profiles.json"
    profiles_payload = (
        {"profiles": "not-an-array"}
        if failure_kind == "invalid_profiles"
        else {
            "profiles": [
                {
                    "profile_id": "local",
                    "provider_id": "ollama",
                    "model": "phi4-mini",
                    "local": True,
                }
            ]
        }
    )
    profiles_path.write_text(
        json.dumps(profiles_payload),
        encoding="utf-8",
    )
    monkeypatch.setattr(svc_mod, "_PROFILE_RESOLVER_CACHE", None)
    monkeypatch.setenv("MODEL_PROFILES_PATH", str(profiles_path))
    monkeypatch.delenv("ANANTA_MODEL_ROUTING_PATH", raising=False)
    if failure_kind == "missing_routing":
        monkeypatch.setenv(
            "MODEL_ROUTING_PATH",
            str(tmp_path / "missing.routing.json"),
        )
    else:
        monkeypatch.delenv("MODEL_ROUTING_PATH", raising=False)

    with pytest.raises(ModelRoutingConfigurationError):
        ModelInvocationService._get_resolver()


def test_implicit_routing_context_disallows_cloud(monkeypatch) -> None:
    from agent.services.model_invocation_service import LLMUnavailableError
    from agent.services.model_profile_loader import ModelProfile
    from agent.services.model_profile_resolver import ModelProfileResolver

    cloud = ModelProfile(
        profile_id="cloud",
        provider_id="openai",
        model="gpt-test",
        cloud=True,
        cloud_allowed=True,
        block_secret_context=True,
    )
    resolver = ModelProfileResolver([cloud])
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_resolver",
        classmethod(lambda cls: resolver),
    )
    monkeypatch.setattr(
        "agent.services.model_invocation_service.requests.post",
        lambda *args, **kwargs: pytest.fail("cloud provider must not be called"),
    )

    with pytest.raises(LLMUnavailableError) as raised:
        ModelInvocationService.invoke_result("ordinary non-secret prompt")

    assert raised.value.terminal_reason == "policy_blocked"
    blocked = raised.value.fallback_decisions[0]["blocked_candidates"]
    assert any(item["reason"] == "security_policy:cloud_disabled_by_routing_context" for item in blocked)


def test_resolve_propose_llm_timeout_seconds_uses_effective_config() -> None:
    cfg = {
        "task_propose_timeout_seconds": 420,
        "command_timeout": 60,
        "task_kind_execution_policies": {"coding": {"command_timeout": 180}},
    }
    timeout = resolve_propose_llm_timeout_seconds(effective_config=cfg, task_kind="coding")
    assert timeout == 420


def test_calibrated_timeout_from_benchmarks_uses_p95_with_buffer(tmp_path) -> None:
    data_dir = str(tmp_path)
    payload = {
        "models": {
            "lmstudio:auto": {
                "provider": "lmstudio",
                "model": "auto",
                "task_kinds": {
                    "coding": {
                        "samples": [
                            {"latency_ms": 10000},
                            {"latency_ms": 12000},
                            {"latency_ms": 18000},
                            {"latency_ms": 25000},
                            {"latency_ms": 30000},
                        ]
                    }
                },
            }
        }
    }
    with open(tmp_path / "llm_model_benchmarks.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    timeout = _calibrated_timeout_from_benchmarks(
        data_dir=data_dir,
        provider="lmstudio",
        model="auto",
        task_kind="coding",
        floor_seconds=60,
        ceiling_seconds=1200,
    )
    assert timeout is not None
    assert timeout >= 83  # 30s p95 * 2.5 + 8s


def test_resolve_propose_timeout_prefers_calibrated_when_higher(tmp_path, monkeypatch) -> None:
    payload = {
        "models": {
            "lmstudio:auto": {
                "provider": "lmstudio",
                "model": "auto",
                "task_kinds": {
                    "coding": {"samples": [{"latency_ms": 60000}, {"latency_ms": 58000}, {"latency_ms": 62000}]}
                },
            }
        }
    }
    with open(tmp_path / "llm_model_benchmarks.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    monkeypatch.setattr("agent.services.propose_runtime_policy._resolve_data_dir", lambda: str(tmp_path))
    cfg = {
        "default_provider": "lmstudio",
        "default_model": "auto",
        "task_propose_timeout_seconds": 120,
    }
    timeout = resolve_propose_llm_timeout_seconds(effective_config=cfg, task_kind="coding")
    assert timeout > 120


def test_make_chat_call_with_routing_ctx_includes_resolution_info(monkeypatch) -> None:
    """AMR-019: routing_ctx passed to make_chat_call produces resolution_info in metadata."""
    import agent.services.model_invocation_service as svc_mod
    from agent.services.model_profile_loader import ModelProfile
    from agent.services.model_profile_resolver import (
        ModelProfileResolver,
        RoutingContext,
        RoutingRules,
        SecurityPolicyChecker,
    )

    profile = ModelProfile(
        profile_id="test-local",
        provider_id="ollama",
        model="qwen:7b",
        local=True,
        cloud=False,
    )
    resolver = ModelProfileResolver(
        profiles=[profile],
        security_policy=SecurityPolicyChecker(),
        routing_rules=RoutingRules(),
    )

    monkeypatch.setattr(ModelInvocationService, "_get_resolver", classmethod(lambda cls: resolver))
    svc_mod._PROFILE_RESOLVER_CACHE = None

    captured: dict = {}

    def _fake_post(  # noqa: ANN001
        url, json, headers, timeout, allow_redirects
    ):
        assert allow_redirects is False
        captured["url"] = url
        captured["body"] = dict(json or {})
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{"message": {"content": "routed ok", "tool_calls": []}, "finish_reason": "stop"}],
                "usage": {},
                "model": "qwen:7b",
            },
        )

    monkeypatch.setattr("agent.services.model_invocation_service.requests.post", _fake_post)
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_settings",
        classmethod(
            lambda cls: SimpleNamespace(
                default_provider="lmstudio",
                default_model="auto",
                lmstudio_url="http://localhost:1234/v1",
                ollama_url="http://localhost:11434/api/generate",
                openai_url="https://api.openai.com/v1",
                openai_api_key=None,
                mock_url="http://mock",
                llm_invoke_timeout_seconds=120,
            )
        ),
    )

    ctx = RoutingContext(request_profile_id="test-local")
    result = ModelInvocationService.invoke_result(prompt="hello", routing_ctx=ctx)

    assert "resolution_info" in result.get("metadata", {}), (
        f"Expected resolution_info in metadata, got: {result.get('metadata', {})}"
    )
    ri = result["metadata"]["resolution_info"]
    assert ri["profile_id"] == "test-local"
    assert ri["resolution_source"] == "request_runtime_override"
    assert ri["resolution_rank"] == 1


def test_profile_request_applies_generation_limits(
    monkeypatch,
) -> None:
    from agent.services.model_profile_loader import ModelProfile
    from agent.services.model_profile_resolver import (
        ModelProfileResolver,
        RoutingContext,
    )

    profile = ModelProfile(
        profile_id="gemma-reasoning",
        provider_id="ollama",
        model="ananta-gemma4-reasoning-8k",
        local=True,
        block_secret_context=False,
        temperature=0.35,
        max_output_tokens=321,
        base_url="http://ollama:11434/v1",
    )
    resolver = ModelProfileResolver([profile])
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_resolver",
        classmethod(lambda cls: resolver),
    )
    captured: dict = {}

    def _fake_post(  # noqa: ANN001
        url, json, headers, timeout, allow_redirects
    ):
        assert allow_redirects is False
        del headers, timeout
        captured["url"] = url
        captured["body"] = dict(json)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [
                    {
                        "message": {"content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {},
                "model": profile.model,
            },
        )

    monkeypatch.setattr(
        "agent.services.model_invocation_service.requests.post",
        _fake_post,
    )

    result = ModelInvocationService.invoke_result(
        "hello",
        system_prompt="Solve carefully.",
        routing_ctx=RoutingContext(request_profile_id=profile.profile_id),
    )

    assert result["content"] == "ok"
    assert captured["url"] == "http://ollama:11434/v1/chat/completions"
    assert captured["body"]["temperature"] == pytest.approx(0.35)
    assert captured["body"]["max_tokens"] == 321
    assert (
        ModelInvocationService._max_output_tokens_for_request(
            profile,
            {"max_completion_tokens_per_call": 100},
        )
        == 100
    )


def test_profile_ollama_generate_uses_signed_exact_native_target(
    monkeypatch,
) -> None:
    from agent.services.model_profile_loader import ModelProfile
    from agent.services.model_profile_resolver import (
        ModelProfileResolver,
        RoutingContext,
    )

    profile = ModelProfile(
        profile_id="phi-native",
        provider_id="ollama",
        model="phi4-mini",
        local=True,
        base_url="http://ollama:11434/api/generate",
        max_output_tokens=64,
    )
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_resolver",
        classmethod(lambda cls: ModelProfileResolver([profile])),
    )
    captured: dict = {}

    def _fake_post(  # noqa: ANN001
        url, json, headers, timeout, allow_redirects
    ):
        assert allow_redirects is False
        captured.update(url=url, body=dict(json))
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "model": "phi4-mini",
                "response": "native ok",
                "done": True,
                "prompt_eval_count": 4,
                "eval_count": 2,
            },
        )

    monkeypatch.setattr(
        "agent.services.model_invocation_service.requests.post",
        _fake_post,
    )

    result = ModelInvocationService.invoke_result(
        "hello",
        routing_ctx=RoutingContext(request_profile_id=profile.profile_id),
    )

    assert result["content"] == "native ok"
    assert captured["url"] == ("http://ollama:11434/api/generate")
    assert captured["body"]["model"] == "phi4-mini"
    assert captured["body"]["prompt"] == "user: hello"
    assert captured["body"]["options"]["num_predict"] == 64
    assert result["usage"]["total_tokens"] == 6


def test_invocation_fallback_chain_local_gemma_qwen(monkeypatch) -> None:
    import agent.services.model_invocation_service as svc_mod
    from agent.services.model_profile_loader import ModelProfile
    from agent.services.model_profile_resolver import ModelProfileResolver, RoutingContext, RoutingRules

    local = ModelProfile(
        profile_id="local_lmstudio_phi_json_worker",
        provider_id="lmstudio",
        model="auto",
        local=True,
        block_secret_context=False,
        supports_json=True,
        fallback_group="local_first_cheap",
        fallback_rank=10,
    )
    gemma = ModelProfile(
        profile_id="openrouter_gemma3_4b_cheap_json",
        provider_id="openrouter",
        model="google/gemma-3-4b-it",
        cloud=True,
        cloud_allowed=True,
        block_secret_context=True,
        supports_json=True,
        fallback_group="local_first_cheap",
        fallback_rank=20,
    )
    qwen = ModelProfile(
        profile_id="openrouter_qwen3_30b_a3b_stronger",
        provider_id="openrouter",
        model="qwen/qwen3-30b-a3b-instruct-2507",
        cloud=True,
        cloud_allowed=True,
        block_secret_context=True,
        supports_json=True,
        fallback_group="local_first_cheap",
        fallback_rank=30,
    )
    resolver = ModelProfileResolver(
        [local, gemma, qwen],
        routing_rules=RoutingRules.from_dict(
            {
                "fallback_groups": {
                    "local_first_cheap": {"ordered_profiles": [local.profile_id, gemma.profile_id, qwen.profile_id]}
                }
            }
        ),
    )
    monkeypatch.setattr(ModelInvocationService, "_get_resolver", classmethod(lambda cls: resolver))
    svc_mod._PROFILE_RESOLVER_CACHE = None
    calls: list[str] = []

    def _fake_post(  # noqa: ANN001
        url, json, headers, timeout, allow_redirects
    ):
        assert allow_redirects is False
        calls.append(json["model"])
        if len(calls) == 1:
            raise svc_mod.requests.exceptions.Timeout("local timeout")
        if len(calls) == 2:
            return SimpleNamespace(status_code=503, text="bad gateway")
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{"message": {"content": "qwen ok", "tool_calls": []}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                "model": "qwen/qwen3-30b-a3b-instruct-2507",
            },
        )

    monkeypatch.setattr("agent.services.model_invocation_service.requests.post", _fake_post)
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_settings",
        classmethod(
            lambda cls: SimpleNamespace(
                default_provider="lmstudio",
                default_model="auto",
                lmstudio_url="http://localhost:1234/v1",
                ollama_url="http://localhost:11434/api/generate",
                openai_url="https://api.openai.com/v1",
                openai_api_key=None,
                mock_url="http://mock",
                llm_invoke_timeout_seconds=120,
            )
        ),
    )

    result = ModelInvocationService.invoke_result(
        "hello",
        routing_ctx=RoutingContext(fallback_group_id="local_first_cheap", allow_cloud=True),
    )

    assert result["content"] == "qwen ok"
    profile = result["metadata"]["llm_call_profile"]
    assert len(profile) == 3
    assert profile[0]["success"] is False
    assert profile[1]["success"] is False
    assert profile[2]["success"] is True
    assert result["metadata"]["resolution_info"]["candidate_chain"] == [
        "local_lmstudio_phi_json_worker",
        "openrouter_gemma3_4b_cheap_json",
        "openrouter_qwen3_30b_a3b_stronger",
    ]
    assert result["metadata"]["resolution_info"]["initial_profile_id"] == ("local_lmstudio_phi_json_worker")
    assert result["metadata"]["resolution_info"]["profile_id"] == ("openrouter_qwen3_30b_a3b_stronger")


def _bound_provider_context(*, model: str, binding_id: str):
    from ananta_contracts.provider_invocation import ProviderInvocationContext

    return ProviderInvocationContext(
        tenant_id="tenant-1",
        run_id="run-1",
        workflow_id="workflow-1",
        step_id="step-1",
        plan_hash="a" * 64,
        authorization_envelope={"schema": "ananta.runtime_authorization.v1"},
        attempt_id="attempt-1",
        fencing_token=1,
        policy_version="policy-v1",
        prompt_version="prompt-v1",
        max_attempts=8,
        require_hub_provider_budget=True,
        selected_provider_id="ollama",
        selected_model_id=model,
        provider_binding_id=binding_id,
        provider_transport_mode="hub_bound",
        provider_decision_reason="hub_provider_policy_selected",
    )


def _local_fallback_resolver(*, phi_retries: int = 0):
    from agent.services.model_profile_loader import ModelProfile
    from agent.services.model_profile_resolver import (
        ModelProfileResolver,
        RoutingRules,
    )

    phi = ModelProfile(
        profile_id="phi",
        provider_id="ollama",
        model="phi",
        local=True,
        retry_budget=phi_retries,
        fallback_group="local",
        base_url="http://ollama:11434/v1",
    )
    gemma = ModelProfile(
        profile_id="gemma",
        provider_id="ollama",
        model="gemma",
        local=True,
        fallback_group="local",
        base_url="http://ollama:11434/v1",
    )
    return ModelProfileResolver(
        [phi, gemma],
        routing_rules=RoutingRules.from_dict(
            {
                "fallback_groups": {
                    "local": {
                        "ordered_profiles": ["phi", "gemma"],
                        "max_total_retries": phi_retries,
                    }
                }
            }
        ),
    )


def _local_lfm_kat_resolver():
    from agent.services.model_profile_loader import ModelProfile
    from agent.services.model_profile_resolver import ModelProfileResolver, RoutingRules

    lfm = ModelProfile(
        profile_id="local_lfm25_agentic_fast",
        provider_id="llamacpp",
        model="lfm2.5-2.6b-agentic-q8_0",
        local=True,
        retry_budget=0,
        fallback_group="local_lfm_kat",
        base_url="http://host.docker.internal:8081/v1",
    )
    kat = ModelProfile(
        profile_id="local_kat_coder_v25_heavy",
        provider_id="openai_compatible",
        model="kat-coder-v2.5-dev",
        local=True,
        retry_budget=0,
        fallback_group="local_lfm_kat",
        base_url="http://host.docker.internal:8082/v1",
    )
    return ModelProfileResolver(
        [lfm, kat],
        routing_rules=RoutingRules.from_dict(
            {
                "fallback_groups": {
                    "local_lfm_kat": {
                        "ordered_profiles": [
                            "local_lfm25_agentic_fast",
                            "local_kat_coder_v25_heavy",
                        ],
                        "max_total_retries": 0,
                    }
                }
            }
        ),
    )


def test_local_lfm_timeout_falls_back_once_to_kat_and_audits_both_attempts(
    monkeypatch,
    app,
) -> None:
    from agent.services.model_invocation_service import LLMUnavailableError
    from agent.services.model_profile_resolver import RoutingContext

    resolver = _local_lfm_kat_resolver()
    monkeypatch.setattr(ModelInvocationService, "_get_resolver", classmethod(lambda cls: resolver))
    attempts: list[str] = []
    observations = []

    class Observer:
        def observe_attempt(self, observation):
            observations.append(observation)

    app.extensions["model_invocation_observation_port"] = Observer()

    def invoke(cls, messages, **kwargs):  # noqa: ANN001
        del cls, messages
        model = kwargs["attempt"]["model"]
        attempts.append(model)
        if model == "lfm2.5-2.6b-agentic-q8_0":
            raise LLMUnavailableError(
                "timeout",
                llm_call_profile=[{"error_type": "timeout", "latency_ms": 1000}],
                terminal_reason="timeout",
            )
        return {
            "choices": [{"message": {"content": "kat recovered"}}],
            "model": model,
            "metadata": {"llm_call_profile": [{"latency_ms": 20}]},
        }

    monkeypatch.setattr(ModelInvocationService, "_make_single_chat_call", classmethod(invoke))
    with app.app_context():
        result = ModelInvocationService.invoke_result(
            "bounded local request",
            routing_ctx=RoutingContext(
                request_profile_id="local_lfm25_agentic_fast",
                fallback_group_id="local_lfm_kat",
            ),
        )

    assert result["content"] == "kat recovered"
    assert attempts == ["lfm2.5-2.6b-agentic-q8_0", "kat-coder-v2.5-dev"]
    assert [item.profile_id for item in observations] == [
        "local_lfm25_agentic_fast",
        "local_kat_coder_v25_heavy",
    ]
    assert [item.success for item in observations] == [False, True]
    assert result["metadata"]["fallback_decisions"][-1]["reason"] == "fallback_allowed"


def _hub_signed_phi_gemma_attempt_plan():
    from ananta_contracts.provider_execution import (
        ProviderProfileAttemptPlanEntry,
    )

    return (
        ProviderProfileAttemptPlanEntry(
            profile_id="phi",
            binding_id=f"provider-binding:{'a' * 64}",
            provider_id="ollama",
            model_id="phi",
            maximum_attempts=3,
        ),
        ProviderProfileAttemptPlanEntry(
            profile_id="gemma",
            binding_id=f"provider-binding:{'b' * 64}",
            provider_id="ollama",
            model_id="gemma",
            maximum_attempts=2,
        ),
    )


@pytest.mark.parametrize(
    "error_type",
    (
        "client_error",
        "policy_blocked",
        "provider_egress_denied",
        "provider_token_budget_exceeded",
        "provider_cost_budget_exceeded",
        "provider_deadline_exceeded",
        "provider_attempt_plan_sequence_denied",
        "unknown_provider_denial",
    ),
)
def test_hub_signed_attempt_plan_fails_closed_for_denied_triggers(
    monkeypatch,
    error_type,
) -> None:
    from agent.services.model_invocation_service import LLMUnavailableError

    resolver = _local_fallback_resolver()
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_resolver",
        classmethod(lambda cls: resolver),
    )
    calls: list[str] = []

    def _denied_call(cls, messages, **values):  # noqa: ANN001
        del cls, messages
        calls.append(values["attempt"]["model"])
        raise LLMUnavailableError(
            error_type,
            llm_call_profile=[{"error_type": error_type}],
            terminal_reason=error_type,
        )

    monkeypatch.setattr(
        ModelInvocationService,
        "_make_single_chat_call",
        classmethod(_denied_call),
    )

    with pytest.raises(LLMUnavailableError) as raised:
        ModelInvocationService.invoke_result(
            "hello",
            provider_attempt_plan=_hub_signed_phi_gemma_attempt_plan(),
        )

    assert calls == ["phi"]
    assert raised.value.terminal_reason == error_type
    assert raised.value.fallback_decisions[-1]["terminal"] is True
    assert raised.value.fallback_decisions[-1]["next_profile_id"] is None


def test_hub_signed_attempt_plan_http_4xx_does_not_call_gemma(
    monkeypatch,
) -> None:
    from agent.services.model_invocation_service import LLMUnavailableError

    resolver = _local_fallback_resolver()
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_resolver",
        classmethod(lambda cls: resolver),
    )
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_settings",
        classmethod(
            lambda cls: SimpleNamespace(
                default_provider="ollama",
                default_model="auto",
                lmstudio_url="",
                ollama_url="http://ollama:11434/v1",
                openai_url="",
                openai_api_key=None,
                mock_url="",
                llm_invoke_timeout_seconds=120,
            )
        ),
    )
    calls: list[str] = []

    def _client_error(  # noqa: ANN001
        url, json, headers, timeout, allow_redirects
    ):
        del url, headers, timeout
        assert allow_redirects is False
        calls.append(json["model"])
        return SimpleNamespace(
            status_code=400,
            text="invalid request",
            url="",
        )

    monkeypatch.setattr(
        "agent.services.model_invocation_service.requests.post",
        _client_error,
    )

    with pytest.raises(LLMUnavailableError) as raised:
        ModelInvocationService.invoke_result(
            "hello",
            provider_attempt_plan=_hub_signed_phi_gemma_attempt_plan(),
        )

    assert calls == ["phi"]
    assert raised.value.terminal_reason == "client_error"


def test_hub_signed_context_overflow_defers_directly_to_hub_recovery(
    monkeypatch,
) -> None:
    from agent.services.model_invocation_service import LLMUnavailableError
    from agent.services.model_recovery_signal import (
        sanitize_terminal_model_recovery_signal,
    )

    resolver = _local_fallback_resolver()
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_resolver",
        classmethod(lambda cls: resolver),
    )
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_settings",
        classmethod(
            lambda cls: SimpleNamespace(
                default_provider="ollama",
                default_model="auto",
                lmstudio_url="",
                ollama_url="http://ollama:11434/v1",
                openai_url="",
                openai_api_key=None,
                mock_url="",
                llm_invoke_timeout_seconds=120,
            )
        ),
    )
    calls: list[str] = []

    def _context_overflow(  # noqa: ANN001
        url, json, headers, timeout, allow_redirects
    ):
        del url, headers, timeout
        assert allow_redirects is False
        calls.append(json["model"])
        return SimpleNamespace(
            status_code=400,
            text="maximum context length exceeded",
            url="",
        )

    monkeypatch.setattr(
        "agent.services.model_invocation_service.requests.post",
        _context_overflow,
    )

    with pytest.raises(LLMUnavailableError) as raised:
        ModelInvocationService.invoke_result(
            "hello",
            provider_attempt_plan=_hub_signed_phi_gemma_attempt_plan(),
        )

    assert calls == ["phi"]
    assert raised.value.terminal_reason == "context_too_large"
    assert raised.value.fallback_decisions[-1]["reason"] == "hub_signed_context_recovery_required"
    assert sanitize_terminal_model_recovery_signal(raised.value.model_recovery_signal) is not None


def test_provider_context_retry_attempt_advances_for_every_profile_request(
    monkeypatch,
) -> None:
    from agent.services.model_invocation_service import LLMUnavailableError
    from agent.services.model_profile_resolver import RoutingContext

    resolver = _local_fallback_resolver(phi_retries=1)
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_resolver",
        classmethod(lambda cls: resolver),
    )
    contexts = []

    def _fake_call(cls, messages, **kwargs):  # noqa: ANN001
        del cls, messages
        contexts.append(kwargs["provider_context"])
        if len(contexts) == 1:
            raise LLMUnavailableError(
                "timeout",
                llm_call_profile=[{"error_type": "timeout"}],
                terminal_reason="timeout",
            )
        return {
            "choices": [{"message": {"content": "ok"}}],
            "metadata": {"llm_call_profile": []},
        }

    monkeypatch.setattr(
        ModelInvocationService,
        "_make_single_chat_call",
        classmethod(_fake_call),
    )

    result = ModelInvocationService.invoke_result(
        "hello",
        routing_ctx=RoutingContext(
            request_profile_id="phi",
            fallback_group_id="local",
        ),
        provider_context=_bound_provider_context(
            model="phi",
            binding_id="binding-phi",
        ),
    )

    assert result["content"] == "ok"
    assert [context.retry_attempt for context in contexts] == [0, 1]
    assert contexts[0].retry_id.endswith(":provider:0")
    assert contexts[1].retry_id.endswith(":provider:1")
    assert contexts[0].selected_model_id == contexts[1].selected_model_id == "phi"


def test_bound_primary_context_cannot_authorize_unbound_fallback(
    monkeypatch,
) -> None:
    from agent.services.model_invocation_service import LLMUnavailableError
    from agent.services.model_profile_resolver import RoutingContext

    resolver = _local_fallback_resolver()
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_resolver",
        classmethod(lambda cls: resolver),
    )
    attempted_models: list[str] = []

    def _fake_call(cls, messages, **kwargs):  # noqa: ANN001
        del cls, messages
        attempted_models.append(kwargs["attempt"]["model"])
        raise LLMUnavailableError(
            "timeout",
            llm_call_profile=[{"error_type": "timeout"}],
            terminal_reason="timeout",
        )

    monkeypatch.setattr(
        ModelInvocationService,
        "_make_single_chat_call",
        classmethod(_fake_call),
    )

    with pytest.raises(LLMUnavailableError) as raised:
        ModelInvocationService.invoke_result(
            "hello",
            routing_ctx=RoutingContext(
                request_profile_id="phi",
                fallback_group_id="local",
            ),
            provider_context=_bound_provider_context(
                model="phi",
                binding_id="binding-phi",
            ),
        )

    assert attempted_models == ["phi"]
    assert raised.value.terminal_reason == "policy_blocked"
    assert any(
        decision["reason"] == "provider_fallback_binding_required" for decision in raised.value.fallback_decisions
    )


def test_hub_bound_fallback_uses_its_own_context_and_reports_actual_profile(
    monkeypatch,
) -> None:
    from agent.services.model_invocation_service import LLMUnavailableError
    from agent.services.model_profile_resolver import RoutingContext

    resolver = _local_fallback_resolver()
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_resolver",
        classmethod(lambda cls: resolver),
    )
    contexts = []

    def _fake_call(cls, messages, **kwargs):  # noqa: ANN001
        del cls, messages
        contexts.append(kwargs["provider_context"])
        attempt = kwargs["attempt"]
        if attempt["model"] == "phi":
            raise LLMUnavailableError(
                "timeout",
                llm_call_profile=[{"error_type": "timeout"}],
                terminal_reason="timeout",
            )
        return {
            "choices": [{"message": {"content": "gemma ok"}}],
            "model": "gemma",
            "metadata": {"llm_call_profile": []},
        }

    monkeypatch.setattr(
        ModelInvocationService,
        "_make_single_chat_call",
        classmethod(_fake_call),
    )

    result = ModelInvocationService.invoke_result(
        "hello",
        routing_ctx=RoutingContext(
            request_profile_id="phi",
            fallback_group_id="local",
        ),
        provider_context=_bound_provider_context(
            model="phi",
            binding_id="binding-phi",
        ),
        provider_contexts_by_profile_id={
            "gemma": _bound_provider_context(
                model="gemma",
                binding_id="binding-gemma",
            )
        },
    )

    assert [context.selected_model_id for context in contexts] == [
        "phi",
        "gemma",
    ]
    assert [context.retry_attempt for context in contexts] == [0, 1]
    assert result["metadata"]["resolution_info"]["initial_profile_id"] == "phi"
    assert result["metadata"]["resolution_info"]["profile_id"] == "gemma"
    assert result["metadata"]["resolution_info"]["model"] == "gemma"


def test_public_model_override_is_policy_blocked_when_profiles_are_active(
    monkeypatch,
) -> None:
    from agent.services.model_invocation_service import LLMUnavailableError
    from agent.services.model_profile_resolver import RoutingContext

    resolver = _local_fallback_resolver()
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_resolver",
        classmethod(lambda cls: resolver),
    )
    monkeypatch.setattr(
        ModelInvocationService,
        "_make_single_chat_call",
        classmethod(lambda cls, messages, **kwargs: pytest.fail("model override must fail before provider invocation")),
    )

    with pytest.raises(LLMUnavailableError) as raised:
        ModelInvocationService.invoke_result(
            "hello",
            model="unbound-model",
            routing_ctx=RoutingContext(fallback_group_id="local"),
        )

    assert raised.value.terminal_reason == "policy_blocked"
    assert raised.value.fallback_decisions[0]["reason"] == ("model_override_not_allowed_with_profile_routing")


def test_group_budget_allows_two_phi_retries_and_one_gemma_retry(monkeypatch) -> None:
    from agent.services.model_profile_loader import ModelProfile
    from agent.services.model_profile_resolver import ModelProfileResolver, RoutingContext, RoutingRules

    phi = ModelProfile(
        profile_id="phi",
        provider_id="ollama",
        model="ananta-phi4-mini-32k",
        local=True,
        retry_budget=2,
        fallback_group="local",
        fallback_rank=10,
    )
    gemma = ModelProfile(
        profile_id="gemma",
        provider_id="ollama",
        model="ananta-gemma4-reasoning-8k",
        local=True,
        retry_budget=1,
        fallback_group="local",
        fallback_rank=20,
    )
    resolver = ModelProfileResolver(
        [phi, gemma],
        routing_rules=RoutingRules.from_dict(
            {
                "fallback_groups": {
                    "local": {
                        "ordered_profiles": ["phi", "gemma"],
                        "max_total_retries": 3,
                    }
                }
            }
        ),
    )
    monkeypatch.setattr(ModelInvocationService, "_get_resolver", classmethod(lambda cls: resolver))
    calls: list[str] = []
    gemma_calls = 0

    def _fake_post(  # noqa: ANN001
        url, json, headers, timeout, allow_redirects
    ):
        assert allow_redirects is False
        nonlocal gemma_calls
        calls.append(json["model"])
        if json["model"] == "ananta-phi4-mini-32k":
            raise requests.exceptions.Timeout("phi timeout")
        gemma_calls += 1
        if gemma_calls == 1:
            raise requests.exceptions.Timeout("gemma timeout")
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": "gemma ok"}}], "usage": {}},
        )

    monkeypatch.setattr("agent.services.model_invocation_service.requests.post", _fake_post)
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_settings",
        classmethod(
            lambda cls: SimpleNamespace(
                default_provider="ollama",
                default_model="auto",
                lmstudio_url="",
                ollama_url="http://ollama:11434/api/generate",
                openai_url="",
                openai_api_key=None,
                mock_url="",
                llm_invoke_timeout_seconds=120,
            )
        ),
    )

    result = ModelInvocationService.invoke_result("hello", routing_ctx=RoutingContext(fallback_group_id="local"))

    assert result["content"] == "gemma ok"
    assert calls == (["ananta-phi4-mini-32k"] * 3 + ["ananta-gemma4-reasoning-8k"] * 2)
    decisions = result["metadata"]["fallback_decisions"]
    assert [item["reason"] for item in decisions] == [
        "same_profile_retry_allowed",
        "same_profile_retry_allowed",
        "fallback_allowed",
        "same_profile_retry_allowed",
    ]
    assert result["metadata"]["resolution_info"]["fallback_group_max_total_retries"] == 3


def test_group_retry_budget_caps_retries_across_all_profiles(monkeypatch) -> None:
    from agent.services.model_invocation_service import LLMUnavailableError
    from agent.services.model_profile_loader import ModelProfile
    from agent.services.model_profile_resolver import (
        ModelProfileResolver,
        RoutingContext,
        RoutingRules,
    )

    phi = ModelProfile(
        profile_id="phi",
        provider_id="ollama",
        model="phi",
        local=True,
        retry_budget=4,
        fallback_group="local",
    )
    gemma = ModelProfile(
        profile_id="gemma",
        provider_id="ollama",
        model="gemma",
        local=True,
        retry_budget=4,
        fallback_group="local",
    )
    resolver = ModelProfileResolver(
        [phi, gemma],
        routing_rules=RoutingRules.from_dict(
            {
                "fallback_groups": {
                    "local": {
                        "ordered_profiles": ["phi", "gemma"],
                        "max_total_retries": 2,
                    }
                }
            }
        ),
    )
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_resolver",
        classmethod(lambda cls: resolver),
    )
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_settings",
        classmethod(
            lambda cls: SimpleNamespace(
                default_provider="ollama",
                default_model="auto",
                lmstudio_url="",
                ollama_url="http://ollama:11434/api/generate",
                openai_url="",
                openai_api_key=None,
                mock_url="",
                llm_invoke_timeout_seconds=120,
            )
        ),
    )
    calls: list[str] = []

    def _always_timeout(  # noqa: ANN001
        url, json, headers, timeout, allow_redirects
    ):
        assert allow_redirects is False
        calls.append(json["model"])
        raise requests.exceptions.Timeout("timeout")

    monkeypatch.setattr(
        "agent.services.model_invocation_service.requests.post",
        _always_timeout,
    )

    with pytest.raises(LLMUnavailableError) as raised:
        ModelInvocationService.invoke_result(
            "hello",
            routing_ctx=RoutingContext(fallback_group_id="local"),
        )

    assert calls == ["phi", "phi", "phi", "gemma"]
    retry_decisions = [
        decision for decision in raised.value.fallback_decisions if decision["reason"] == "same_profile_retry_allowed"
    ]
    assert [decision["group_retries_used"] for decision in retry_decisions] == [1, 2]


def test_json_schema_failure_retries_phi_then_uses_gemma(monkeypatch) -> None:
    from agent.services.model_profile_loader import ModelProfile
    from agent.services.model_profile_resolver import ModelProfileResolver, RoutingContext, RoutingRules

    phi = ModelProfile(
        profile_id="phi",
        provider_id="ollama",
        model="phi4-mini",
        local=True,
        supports_json=True,
        retry_budget=2,
        fallback_group="local",
        fallback_rank=10,
    )
    gemma = ModelProfile(
        profile_id="gemma",
        provider_id="ollama",
        model="gemma4",
        local=True,
        supports_json=True,
        fallback_group="local",
        fallback_rank=20,
    )
    resolver = ModelProfileResolver(
        [phi, gemma],
        routing_rules=RoutingRules.from_dict(
            {
                "fallback_groups": {
                    "local": {
                        "ordered_profiles": ["phi", "gemma"],
                        "max_total_retries": 3,
                    }
                }
            }
        ),
    )
    monkeypatch.setattr(ModelInvocationService, "_get_resolver", classmethod(lambda cls: resolver))
    calls: list[str] = []

    def _fake_post(  # noqa: ANN001
        url, json, headers, timeout, allow_redirects
    ):
        assert allow_redirects is False
        calls.append(json["model"])
        content = "{not-json" if json["model"] == "phi4-mini" else '{"command":"echo ok"}'
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {},
                "model": json["model"],
            },
        )

    monkeypatch.setattr("agent.services.model_invocation_service.requests.post", _fake_post)
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_settings",
        classmethod(
            lambda cls: SimpleNamespace(
                default_provider="ollama",
                default_model="auto",
                lmstudio_url="",
                ollama_url="http://ollama:11434/api/generate",
                openai_url="",
                openai_api_key=None,
                mock_url="",
                llm_invoke_timeout_seconds=120,
            )
        ),
    )

    result = ModelInvocationService.invoke_with_json_schema_result(
        "answer",
        {
            "type": "object",
            "required": ["command"],
            "properties": {"command": {"type": "string"}},
        },
        retry_on_contract_error=True,
        routing_ctx=RoutingContext(fallback_group_id="local", requires_json=True),
    )

    assert result["structured_output"] == {"command": "echo ok"}
    assert calls == ["phi4-mini", "phi4-mini", "phi4-mini", "gemma4"]
    assert [item["trigger"] for item in result["metadata"]["fallback_decisions"]] == [
        "schema_validation_failed",
        "schema_validation_failed",
        "schema_validation_failed",
    ]


def test_prompt_json_tool_call_schema_failure_is_terminal_model_signal(monkeypatch) -> None:
    from agent.services.model_invocation_service import LLMUnavailableError
    from agent.services.model_profile_loader import ModelProfile
    from agent.services.model_profile_resolver import ModelProfileResolver, RoutingContext

    profile = ModelProfile(
        profile_id="local_prompt_json",
        provider_id="lmstudio",
        model="auto",
        local=True,
        block_secret_context=False,
        supports_json=True,
        supports_tools=False,
        tool_calling_mode="prompt_json",
    )
    resolver = ModelProfileResolver([profile])
    monkeypatch.setattr(ModelInvocationService, "_get_resolver", classmethod(lambda cls: resolver))

    def _fake_post(  # noqa: ANN001
        url, json, headers, timeout, allow_redirects
    ):
        assert allow_redirects is False
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [
                    {
                        "message": {"content": '{"tool":"file_read","args":{"path":123}}', "tool_calls": []},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {},
                "model": "auto",
            },
        )

    monkeypatch.setattr("agent.services.model_invocation_service.requests.post", _fake_post)
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_settings",
        classmethod(
            lambda cls: SimpleNamespace(
                default_provider="lmstudio",
                default_model="auto",
                lmstudio_url="http://localhost:1234/v1",
                ollama_url="http://localhost:11434/api/generate",
                openai_url="https://api.openai.com/v1",
                openai_api_key=None,
                mock_url="http://mock",
                llm_invoke_timeout_seconds=120,
            )
        ),
    )

    with pytest.raises(LLMUnavailableError) as raised:
        ModelInvocationService.invoke_with_tools(
            "choose",
            tools=[
                {
                    "name": "file_read",
                    "description": "read",
                    "parameters": {
                        "type": "object",
                        "required": ["path"],
                        "properties": {"path": {"type": "string"}},
                    },
                }
            ],
            retry_on_contract_error=True,
            routing_ctx=RoutingContext(request_profile_id="local_prompt_json", requires_tools=True),
        )

    exc = raised.value
    assert exc.terminal_reason == "tool_args_invalid"
    assert exc.fallback_decisions[-1]["reason"] == "candidate_chain_exhausted"
    assert exc.model_recovery_signal["schema"] == "model_recovery_signal.v1"
    assert exc.model_recovery_signal["attempt_count"] == 1
