from __future__ import annotations

import json
import logging
from dataclasses import replace

import pytest

from agent.services.local_runtime_capability_cache import (
    LocalRuntimeCapabilityCache,
)
from agent.services.local_runtime_capability_contracts import (
    RuntimeCapabilityClaim,
    RuntimeModelSnapshot,
)
from agent.services.local_runtime_capability_discovery import (
    LmStudioCapabilityDiscoveryAdapter,
    LocalRuntimeRefreshCoordinator,
    OllamaCapabilityDiscoveryAdapter,
)
from agent.services.local_runtime_capability_inventory_adapter import (
    LocalRuntimeCapabilityInventoryAdapter,
)
from agent.services.local_runtime_capability_normalizer import (
    LocalRuntimeCapabilityNormalizer,
)
from agent.services.local_runtime_capability_result_ingestor import (
    LocalRuntimeCapabilityResultIngestor,
)
from agent.services.local_runtime_capability_task_dispatcher import (
    LocalRuntimeCapabilityRefreshDispatcher,
)
from agent.services.local_runtime_http_client import (
    LocalRuntimeEndpointPolicy,
    LocalRuntimeHttpClient,
    LocalRuntimeTransportError,
)
from agent.services.local_runtime_image_adapter import (
    LocalRuntimeImage,
    LocalRuntimeImageAdapter,
)
from agent.services.local_runtime_request_policy import LocalRuntimeRequestPolicy
from agent.services.local_runtime_response_adapters import (
    LocalRuntimeResponseError,
    OllamaChatStreamAccumulator,
    normalize_ollama_chat,
    normalize_ollama_embedding,
    normalize_ollama_generate,
    normalize_openai_chat,
)
from agent.services.local_runtime_routing_policy import LocalRuntimeRoutingPolicy
from agent.services.local_runtime_template_inspector import (
    LocalRuntimeTemplateInspector,
)
from worker.local_runtime_capability_handler import (
    LocalRuntimeCapabilityRefreshHandler,
)
from worker.retrieval.embedding_provider import (
    EmbeddingProviderRequestFailed,
    build_embedding_provider,
)

NOW = "2026-08-31T00:00:00Z"


def _snapshot(*, provider="ollama", model="model", capabilities=("chat",), stale=False):
    return RuntimeModelSnapshot(
        provider_id=provider,
        model_id=model,
        model_digest="a" * 64,
        runtime_version="1",
        model_kind="chat",
        context_window=8192,
        template_family="chatml",
        template_sha256="b" * 64,
        capabilities=tuple(RuntimeCapabilityClaim(name, True, "runtime_reported", 1.0, NOW) for name in capabilities),
        conflicts=(),
        discovered_at=NOW,
        stale=stale,
    )


@pytest.mark.parametrize(
    ("template", "family"),
    [
        ("<|im_start|>user<|im_end|>", "chatml"),
        ("<|start_header_id|>user<|eot_id|>", "llama3"),
        ("[INST]hello[/INST]", "mistral"),
        ("<start_of_turn>user<end_of_turn>", "gemma"),
        ("<|im_start|>system<tool_call><|im_end|>", "hermes"),
        ("<|system|>x<|assistant|>", "phi"),
    ],
)
def test_template_inspector_classifies_without_exposing_raw_template(template, family):
    result = LocalRuntimeTemplateInspector().inspect(template)
    assert result.family == family
    assert result.sha256 and template not in result.sha256


def test_template_inspector_detects_incompatible_control_tokens():
    result = LocalRuntimeTemplateInspector().inspect("[INST]x[/INST]<|start_header_id|>y<|eot_id|>")
    assert result.family == "conflict"
    assert result.conflict is True


def test_normalizer_uses_only_positive_runtime_fields_and_separates_embeddings():
    normalizer = LocalRuntimeCapabilityNormalizer()
    chat = normalizer.normalize(
        provider_id="ollama",
        model_id="chat-model",
        runtime_version="0.12",
        model_digest="1" * 64,
        discovered_at=NOW,
        metadata={"capabilities": ["completion", "tools"], "context_length": 4096},
    )
    embedding = normalizer.normalize(
        provider_id="lmstudio",
        model_id="vector-model",
        runtime_version="1",
        discovered_at=NOW,
        metadata={"type": "embedding", "supports_embedding": True, "supports_chat": True},
    )
    assert chat.routable("chat") and chat.routable("tools")
    assert chat.claim("vision") is None
    assert embedding.model_kind == "embedding"
    assert embedding.routable("chat") is False
    assert "embedding_chat_conflict" in embedding.conflicts


def test_heuristics_never_become_routable():
    heuristic = replace(
        _snapshot(),
        capabilities=(RuntimeCapabilityClaim("tools", True, "heuristic", 0.8, NOW),),
    )
    assert heuristic.routable("tools") is False


def test_unknown_capability_extension_round_trips_but_never_routes():
    snapshot = replace(
        _snapshot(),
        capabilities=(
            RuntimeCapabilityClaim("future.audio", True, "runtime_reported", 1.0, NOW),
        ),
    )
    restored = RuntimeModelSnapshot.from_mapping(snapshot.to_dict())
    assert restored.claim("future.audio") is not None
    assert restored.routable("future.audio") is False


def test_observed_failure_temporarily_blocks_a_profile_claim():
    snapshot = replace(
        _snapshot(),
        capabilities=(
            RuntimeCapabilityClaim("tools", True, "profile_declared", 0.9, NOW),
            RuntimeCapabilityClaim(
                "tools",
                False,
                "observed_failure",
                1.0,
                NOW,
                "2099-01-01T00:00:00Z",
            ),
        ),
    )
    assert snapshot.routable("tools") is False

    expired = replace(
        snapshot,
        capabilities=(
            snapshot.capabilities[0],
            RuntimeCapabilityClaim(
                "tools",
                False,
                "observed_failure",
                1.0,
                "2020-01-01T00:00:00Z",
                "2020-01-02T00:00:00Z",
            ),
        ),
    )
    assert expired.routable("tools") is True


def test_snapshot_digest_detects_tampering():
    raw = _snapshot().to_dict()
    raw["context_window"] = 999
    with pytest.raises(ValueError, match="content_digest_mismatch"):
        RuntimeModelSnapshot.from_mapping(raw)


def test_cache_is_atomic_bounded_and_corruption_safe(tmp_path):
    path = tmp_path / "runtime-capabilities.json"
    cache = LocalRuntimeCapabilityCache(path, maximum_models=2)
    cache.save((_snapshot(model="a"), _snapshot(model="b"), _snapshot(model="c")))
    assert [item.model_id for item in cache.load()] == ["b", "c"]
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_text("{broken", encoding="utf-8")
    assert cache.load() == ()


def test_capability_cache_projects_into_canonical_model_inventory(tmp_path):
    cache = LocalRuntimeCapabilityCache(tmp_path / "cache.json")
    cache.save((_snapshot(capabilities=("chat", "tools", "vision")),))
    descriptor = LocalRuntimeCapabilityInventoryAdapter(cache).collect().models[0]
    assert descriptor.provider_id == "ollama"
    assert descriptor.input_modalities == ("image", "text")
    assert {item.capability_id: item.evidence.value for item in descriptor.capabilities} == {
        "chat": "detected",
        "tools": "detected",
        "vision": "detected",
    }
    assert any(item.fact_id == "template.family" for item in descriptor.metadata_facts)


class _Client:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request_json(self, method, base_url, path, **kwargs):
        self.calls.append((method, path, kwargs.get("payload")))
        value = self.responses[path]
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value(kwargs.get("payload"))
        return value


class _Queue:
    def __init__(self):
        self.tasks = []

    def ingest_task(self, **task):
        self.tasks.append(task)


def test_ollama_detail_discovery_is_bounded_and_detail_failure_keeps_model():
    client = _Client({
        "/api/tags": {"models": [
            {"name": "chat", "digest": "1" * 64},
            {"name": "chat", "digest": "1" * 64},
            {"name": "embed", "digest": "2" * 64},
        ]},
        "/api/show": lambda payload: (
            {"capabilities": ["completion", "tools"], "template": "<|im_start|>x<|im_end|>"}
            if payload["model"] == "chat"
            else {"capabilities": ["embedding"], "type": "embedding"}
        ),
    })
    snapshots = OllamaCapabilityDiscoveryAdapter(
        client=client, base_url="http://127.0.0.1:11434", runtime_version="1"
    ).discover()
    assert [item.model_id for item in snapshots] == ["chat", "embed"]
    assert len([call for call in client.calls if call[1] == "/api/show"]) == 2
    assert snapshots[1].model_kind == "embedding"


def test_ollama_detail_failure_keeps_tag_model_without_invented_capabilities():
    client = _Client(
        {
            "/api/tags": {"models": [{"name": "chat", "digest": "1" * 64}]},
            "/api/show": LocalRuntimeTransportError("local_runtime_request_failed"),
        }
    )
    snapshots = OllamaCapabilityDiscoveryAdapter(
        client=client,
        base_url="http://127.0.0.1:11434",
        runtime_version="1",
    ).discover()
    assert len(snapshots) == 1
    assert snapshots[0].model_id == "chat"
    assert snapshots[0].capabilities == ()


def test_lmstudio_native_metadata_is_optional_and_does_not_use_name_heuristics():
    client = _Client({
        "/v1/models": {"data": [{"id": "not-named-like-an-embedding"}, {"id": "chat"}]},
        "/api/v1/models": {"models": [
            {"id": "not-named-like-an-embedding", "type": "embedding", "capabilities": ["embedding"]},
            {"id": "chat", "type": "llm", "capabilities": ["chat"]},
        ]},
    })
    snapshots = LmStudioCapabilityDiscoveryAdapter(
        client=client, base_url="http://127.0.0.1:1234", runtime_version="1"
    ).discover()
    assert {item.model_id: item.model_kind for item in snapshots} == {
        "chat": "chat",
        "not-named-like-an-embedding": "embedding",
    }


def test_lmstudio_native_endpoint_failure_falls_back_to_compatible_catalog():
    client = _Client(
        {
            "/v1/models": {"data": [{"id": "chat", "capabilities": ["chat"]}]},
            "/api/v1/models": LocalRuntimeTransportError("local_runtime_request_failed"),
        }
    )
    snapshots = LmStudioCapabilityDiscoveryAdapter(
        client=client,
        base_url="http://127.0.0.1:1234",
        runtime_version="1",
    ).discover()
    assert [item.model_id for item in snapshots] == ["chat"]
    assert snapshots[0].routable("chat") is True


def test_hub_dispatch_worker_probe_and_hub_acceptance_are_automatic(tmp_path):
    queue = _Queue()
    dispatcher = LocalRuntimeCapabilityRefreshDispatcher(
        provider_urls={"ollama": "http://127.0.0.1:11434/api/generate"},
        queue=queue,
        clock=lambda: 60.0,
    )
    task_id = dispatcher.dispatch(provider_id="ollama", requested_by="operator")
    assert dispatcher.dispatch(provider_id="ollama", requested_by="operator") == task_id
    assert len(queue.tasks) == 1
    queued = queue.tasks[0]
    target = queued["extra_fields"]["worker_execution_context"][
        "local_runtime_capability_refresh"
    ]["targets"][0]
    assert target["base_url"] == "http://127.0.0.1:11434"

    client = _Client(
        {
            "/api/tags": {"models": [{"name": "chat", "digest": "1" * 64}]},
            "/api/show": {
                "capabilities": ["completion", "tools"],
                "template": "<|im_start|>x<|im_end|>",
            },
        }
    )
    worker = LocalRuntimeCapabilityRefreshHandler(
        cache_path=tmp_path / "worker.json",
        client_factory=lambda _policy: client,
    )
    task = {
        "id": task_id,
        "task_kind": queued["extra_fields"]["task_kind"],
        "worker_execution_context": queued["extra_fields"]["worker_execution_context"],
    }
    result = worker.execute(task=task)
    assert result["status"] == "completed"

    hub_cache = LocalRuntimeCapabilityCache(tmp_path / "hub.json")
    accepted = LocalRuntimeCapabilityResultIngestor(hub_cache).accept(
        task=task,
        response=result,
    )
    assert accepted["model_count"] == 1
    assert hub_cache.load()[0].routable("tools") is True


class _Adapter:
    def __init__(self, provider_id, result=None, error=None):
        self.provider_id = provider_id
        self.result = result or ()
        self.error = error

    def discover(self):
        if self.error:
            raise self.error
        return self.result


def test_refresh_failure_isolated_and_cached_state_becomes_stale(tmp_path):
    cache = LocalRuntimeCapabilityCache(tmp_path / "cache.json")
    cache.save((_snapshot(provider="ollama"),))
    coordinator = LocalRuntimeRefreshCoordinator(
        (
            _Adapter("ollama", error=RuntimeError("secret must not leak")),
            _Adapter("lmstudio", result=(_snapshot(provider="lmstudio"),)),
        ),
        cache,
    )
    results = {item.provider_id: item for item in coordinator.refresh_all()}
    assert results["ollama"].status == "stale"
    assert results["ollama"].reason_code == "local_runtime_discovery_failed"
    assert results["ollama"].snapshots[0].stale is True
    assert results["lmstudio"].status == "healthy"
    assert {item.provider_id for item in cache.load()} == {"ollama", "lmstudio"}


def test_parallel_provider_refreshes_do_not_lose_cache_entries(tmp_path):
    cache = LocalRuntimeCapabilityCache(tmp_path / "cache.json")
    coordinator = LocalRuntimeRefreshCoordinator(
        (
            _Adapter("ollama", result=(_snapshot(provider="ollama"),)),
            _Adapter("lmstudio", result=(_snapshot(provider="lmstudio"),)),
        ),
        cache,
    )

    assert {item.status for item in coordinator.refresh_all()} == {"healthy"}
    assert {item.provider_id for item in cache.load()} == {"ollama", "lmstudio"}


def test_routing_requires_every_nonheuristic_capability():
    snapshots = (_snapshot(model="chat"), _snapshot(model="tools", capabilities=("chat", "tools")))
    result = LocalRuntimeRoutingPolicy().select(snapshots, required_capabilities=frozenset({"chat", "tools"}))
    assert [item.model_id for item in result] == ["tools"]


def test_request_policy_uses_smallest_context_and_counts_nested_payloads():
    policy = LocalRuntimeRequestPolicy(maximum_payload_bytes=1024)
    assert policy.effective_context_window(8192, 4096, None) == 4096
    assert policy.validate_payload(
        {
            "messages": [{"content": "text"}],
            "tools": [{"function": {"parameters": {"type": "object"}}}],
            "images": ["YWJj"],
            "tool_results": [{"content": "result"}],
        }
    ) > 0
    with pytest.raises(ValueError, match="payload_too_large"):
        policy.validate_payload({"messages": [{"content": "x" * 2000}]})


def test_response_normalizers_preserve_structured_tools_thinking_and_usage():
    ollama = normalize_ollama_chat({
        "message": {"content": "done", "thinking": "private", "tool_calls": [
            {"function": {"name": "lookup", "arguments": {"id": 1}}}
        ]},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 3,
        "eval_count": 4,
    })
    openai = normalize_openai_chat({
        "choices": [{"message": {"content": "done", "tool_calls": [
            {"id": "x", "function": {"name": "lookup", "arguments": "{\"id\":1}"}}
        ]}, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
    })
    generate = normalize_ollama_generate({"response": "done", "done": True})
    assert ollama["tool_calls"] == [{"id": "call-0", "name": "lookup", "arguments": {"id": 1}}]
    assert openai["tool_calls"][0]["id"] == "x"
    assert generate["tool_calls"] == []
    assert ollama["thinking"] == "private"


def test_ollama_streaming_keeps_thinking_content_tools_and_final_usage_consistent():
    stream = OllamaChatStreamAccumulator()
    stream.push(
        {
            "message": {"content": "", "thinking": "plan "},
            "done": False,
        }
    )
    stream.push(
        {
            "message": {
                "content": "done",
                "tool_calls": [
                    {"id": "x", "function": {"name": "lookup", "arguments": {"id": 1}}}
                ],
            },
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 3,
            "eval_count": 4,
        }
    )
    result = stream.result()
    assert result["content"] == "done"
    assert result["thinking"] == "plan "
    assert result["tool_calls"][0]["id"] == "x"
    assert result["usage"] == {"prompt_tokens": 3, "completion_tokens": 4}
    assert result["finish_reason"] == "stop"


def test_embedding_dimension_and_finite_values_are_enforced():
    assert normalize_ollama_embedding({"embeddings": [[1, 2]]}, expected_dimension=2) == (1.0, 2.0)
    with pytest.raises(LocalRuntimeResponseError, match="dimension_mismatch"):
        normalize_ollama_embedding({"embeddings": [[1, 2]]}, expected_dimension=3)
    with pytest.raises(LocalRuntimeResponseError, match="response_invalid"):
        normalize_ollama_embedding({"embeddings": [[float("nan")]]})


def test_ollama_embedding_provider_uses_native_embed_without_truncation(monkeypatch):
    captured = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return b'{"embeddings":[[1,2],[3,4]]}'

    class _Opener:
        def open(self, req, **_kwargs):
            captured.update({"url": req.full_url, "payload": json.loads(req.data)})
            return _Response()

    monkeypatch.setattr(
        "worker.retrieval.embedding_provider.request.build_opener",
        lambda *_args: _Opener(),
    )
    provider = build_embedding_provider(
        {
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434/api/generate",
            "allowed_base_urls": ["http://127.0.0.1:11434"],
            "model": "embedding-model",
            "dimensions": 2,
        }
    )

    assert provider.embed_texts(["a", "b"]) == [[1.0, 2.0], [3.0, 4.0]]
    assert captured == {
        "url": "http://127.0.0.1:11434/api/embed",
        "payload": {"model": "embedding-model", "input": ["a", "b"], "truncate": False},
    }


def test_ollama_embedding_provider_rejects_dimension_mismatch(monkeypatch):
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return b'{"embeddings":[[1]]}'

    class _Opener:
        def open(self, *_args, **_kwargs):
            return _Response()

    monkeypatch.setattr(
        "worker.retrieval.embedding_provider.request.build_opener",
        lambda *_args: _Opener(),
    )
    provider = build_embedding_provider(
        {
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "allowed_base_urls": ["http://127.0.0.1:11434"],
            "model": "embedding-model",
            "dimensions": 2,
        }
    )

    with pytest.raises(EmbeddingProviderRequestFailed, match="embedding_dimension_mismatch"):
        provider.embed_texts(["a"])


def test_image_adapter_requires_positive_vision_and_bounds_payload():
    adapter = LocalRuntimeImageAdapter(maximum_bytes=3)
    with pytest.raises(ValueError, match="vision_capability_required"):
        adapter.encode(LocalRuntimeImage("image/png", b"x"), snapshot=_snapshot())
    vision = _snapshot(capabilities=("chat", "vision"))
    assert adapter.encode(LocalRuntimeImage("image/png", b"abc"), snapshot=vision) == "YWJj"
    messages = adapter.attach_to_ollama_messages(
        [{"role": "user", "content": "describe"}],
        [LocalRuntimeImage("image/png", b"abc")],
        snapshot=vision,
    )
    assert messages == [{"role": "user", "content": "describe", "images": ["YWJj"]}]
    with pytest.raises(ValueError, match="size_denied"):
        adapter.encode(LocalRuntimeImage("image/png", b"abcd"), snapshot=vision)


def test_snapshot_wire_never_contains_runtime_url_or_raw_template():
    raw = json.dumps(_snapshot().to_dict())
    assert "base_url" not in raw
    assert "<|im_start|>" not in raw


def test_projection_exposes_component_health_with_stable_reason_codes(tmp_path):
    from agent.services.local_runtime_capability_projection import (
        LocalRuntimeCapabilityProjection,
    )

    empty = LocalRuntimeCapabilityProjection(
        LocalRuntimeCapabilityCache(tmp_path / "empty.json")
    ).snapshot()
    assert empty["health"]["cache"]["reason_code"] == "local_runtime_capability_cache_empty"
    assert empty["health"]["routing"]["reason_code"] == "local_runtime_no_routable_model"


def test_runtime_http_policy_denies_private_and_redirects_without_auth_forward(monkeypatch):
    monkeypatch.setattr(
        "agent.services.local_runtime_http_client.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 11434))],
    )
    denied = LocalRuntimeEndpointPolicy(
        frozenset({"http://runtime:11434"}),
        allow_loopback=False,
    )
    with pytest.raises(LocalRuntimeTransportError, match="loopback_denied"):
        denied.admit("http://runtime:11434")

    class _Response:
        status_code = 302
        headers = {"Location": "http://attacker.invalid/steal"}

    class _Session:
        trust_env = True

        def __init__(self):
            self.calls = []

        def request(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return _Response()

    session = _Session()
    client = LocalRuntimeHttpClient(
        LocalRuntimeEndpointPolicy(frozenset({"http://runtime:11434"})),
        session=session,
    )
    with pytest.raises(LocalRuntimeTransportError, match="redirect_forbidden"):
        client.request_json(
            "GET",
            "http://runtime:11434",
            "/api/tags",
            timeout_seconds=1,
            authorization="Bearer secret",
        )
    assert len(session.calls) == 1
    assert session.calls[0][1]["allow_redirects"] is False
    assert session.trust_env is False


def test_runtime_http_policy_rejects_declared_oversize_before_body_read(monkeypatch):
    monkeypatch.setattr(
        "agent.services.local_runtime_http_client.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 11434))],
    )

    class _Response:
        status_code = 200
        headers = {"Content-Length": "11"}

        def raise_for_status(self):
            return None

        def iter_content(self, **_kwargs):
            raise AssertionError("oversize body must not be read")

    class _Session:
        trust_env = True

        def request(self, *_args, **_kwargs):
            return _Response()

    client = LocalRuntimeHttpClient(
        LocalRuntimeEndpointPolicy(
            frozenset({"http://runtime:11434"}),
            maximum_response_bytes=10,
        ),
        session=_Session(),
    )
    with pytest.raises(LocalRuntimeTransportError, match="response_too_large"):
        client.request_json(
            "GET",
            "http://runtime:11434",
            "/api/tags",
            timeout_seconds=1,
        )


class _Projection:
    def snapshot(self):
        return {"schema": "ananta.local-runtime-capability-catalog.v1", "snapshots": []}


class _Dispatch:
    def __init__(self):
        self.requests = []

    def dispatch(self, **request):
        self.requests.append(request)
        return "task-runtime-refresh-1"


def test_runtime_capability_api_reads_projection_and_dispatches_hub_task(client, admin_auth_header):
    projection = _Projection()
    dispatch = _Dispatch()
    client.application.extensions["local_runtime_capability_projection"] = projection
    client.application.extensions["local_runtime_capability_refresh_dispatch"] = dispatch

    read = client.get("/api/models/runtime-capabilities/v1", headers=admin_auth_header)
    refresh = client.post(
        "/api/models/runtime-capabilities/v1/refresh",
        json={"provider_id": "ollama"},
        headers=admin_auth_header,
    )

    assert read.status_code == 200
    assert read.get_json()["data"]["schema"] == "ananta.local-runtime-capability-catalog.v1"
    assert refresh.status_code == 202
    assert refresh.get_json()["data"]["task_ref"] == "task-runtime-refresh-1"
    assert dispatch.requests[0]["provider_id"] == "ollama"


def test_runtime_capability_refresh_rejects_client_provider_authority(client, admin_auth_header):
    client.application.extensions["local_runtime_capability_refresh_dispatch"] = _Dispatch()
    response = client.post(
        "/api/models/runtime-capabilities/v1/refresh",
        json={"provider_id": "remote-untrusted"},
        headers=admin_auth_header,
    )
    assert response.status_code == 400


def test_lmstudio_strategy_preserves_tool_only_response_as_structured_metadata(monkeypatch):
    from agent.llm_strategies.lmstudio import LMStudioStrategy

    strategy = LMStudioStrategy()
    monkeypatch.setattr(
        strategy,
        "_post_lmstudio",
        lambda *_args, **_kwargs: {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {
                                    "name": "lookup",
                                    "arguments": '{"id":1}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1},
        },
    )
    monkeypatch.setattr(strategy, "_update_lmstudio_history", lambda *_args: None)

    result = strategy._call_with_model(
        "model",
        4096,
        "prompt",
        "http://runtime/v1/chat/completions",
        True,
        None,
        5,
    )

    assert result["text"] == ""
    assert result["metadata"]["finish_reason"] == "tool_calls"
    assert result["metadata"]["tool_calls"] == [
        {"id": "call-1", "name": "lookup", "arguments": {"id": 1}}
    ]
    assert strategy._strategy_result_has_output(result) is True


def test_lmstudio_transport_does_not_log_raw_provider_payload(monkeypatch, caplog):
    from agent.llm_strategies.lmstudio import LMStudioStrategy

    secret = "tool-argument-secret"

    class _Response:
        status_code = 200
        headers = {"Content-Length": "64"}
        content = b'{"choices":[]}'
        text = content.decode()

        def json(self):
            return {"choices": [], "sensitive": secret}

    class _Session:
        def post(self, *_args, **_kwargs):
            return _Response()

        def close(self):
            return None

    monkeypatch.setattr(
        "agent.services.lmstudio_request_registry.create_and_register_session",
        lambda: (_Session(), "request-1"),
    )
    monkeypatch.setattr(
        "agent.services.lmstudio_request_registry.release_session",
        lambda *_args: None,
    )
    caplog.set_level(logging.DEBUG)

    payload = LMStudioStrategy()._post_lmstudio(
        "http://runtime/v1/chat/completions",
        {"model": "model"},
        5,
    )

    assert payload["sensitive"] == secret
    assert secret not in caplog.text


def test_ollama_strategy_normalizes_native_chat_tool_calls(monkeypatch):
    from agent.llm_strategies.standard import OllamaStrategy

    class _Response:
        status_code = 200
        text = ""

        def json(self):
            return {
                "message": {
                    "content": "",
                    "thinking": "private",
                    "tool_calls": [
                        {"function": {"name": "lookup", "arguments": {"id": 1}}}
                    ],
                },
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 2,
                "eval_count": 1,
            }

    captured = {}

    def _post(url, payload, **_kwargs):
        captured.update({"url": url, "payload": payload})
        return _Response()

    monkeypatch.setattr("agent.llm_strategies.standard._http_post", _post)
    result = OllamaStrategy().execute(
        model="model",
        prompt="prompt",
        url="http://runtime/api/chat",
        api_key=None,
        history=None,
        timeout=5,
        tools=[
            {
                "type": "function",
                "function": {"name": "lookup", "parameters": {"type": "object"}},
            }
        ],
    )

    assert "messages" in captured["payload"] and "prompt" not in captured["payload"]
    assert result["metadata"]["tool_calls"][0]["name"] == "lookup"
    assert result["metadata"]["thinking"] == "private"


def test_model_invocation_normalizes_ollama_native_chat_to_openai_shape():
    from agent.services.model_invocation_service import ModelInvocationService

    result = ModelInvocationService._normalize_ollama_chat_response(
        {
            "model": "model",
            "message": {
                "content": "",
                "tool_calls": [
                    {"id": "x", "function": {"name": "lookup", "arguments": {"id": 1}}}
                ],
            },
            "done": True,
            "done_reason": "stop",
        },
        model="fallback",
    )

    function = result["choices"][0]["message"]["tool_calls"][0]["function"]
    assert function == {"name": "lookup", "arguments": '{"id":1}'}
