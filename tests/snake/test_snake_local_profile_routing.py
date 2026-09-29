from agent.models import TaskStepProposeRequest
from agent.routes.snakes_chat_helpers import SnakeAskLimits
from agent.routes.snakes_worker_routing import (
    _worker_profile_chat,
    _worker_propose,
    resolve_snake_routing_task_kind,
)
from agent.services.task_execution_service import TaskExecutionService
from agent.services.tiny_router.snake_shadow import observe_snake_candidate


def test_snake_task_kind_routes_code_to_heavy_and_chat_to_fast() -> None:
    assert resolve_snake_routing_task_kind("Erkläre mir kurz Ananta") == "classification"
    assert resolve_snake_routing_task_kind("Analysiere den Code im Repository") == "repo_analysis"
    assert resolve_snake_routing_task_kind("Debug diesen Stacktrace") == "debugging"


def test_worker_propose_delegates_hub_routing_kind_without_legacy_model(monkeypatch) -> None:
    captured = {}

    def forward(worker_url, path, payload, *, token, timeout=None):
        captured.update(worker_url=worker_url, path=path, payload=payload, token=token)
        return {"data": {"reason": "ok"}}

    monkeypatch.setenv("ANANTA_AI_SNAKE_PROFILE_ROUTING", "true")
    monkeypatch.setattr("agent.services.task_runtime_service.forward_to_worker", forward)

    answer, trace = _worker_propose(
        "Analysiere den Code im Repository",
        "legacy-model",
        provider="lmstudio",
        limits=SnakeAskLimits(),
        worker_picker=lambda: ("http://worker:5000", "token"),
        model_resolver=lambda _: (_ for _ in ()).throw(AssertionError("legacy resolver called")),
    )

    assert answer == "ok"
    assert captured["payload"]["provider"] == "ananta_profile"
    assert captured["payload"]["routing_task_kind"] == "repo_analysis"
    assert "model" not in captured["payload"]
    assert trace["routing_source"] == "hub_snake_profile_policy"


def test_worker_propose_fails_over_when_first_worker_rejects_auth(monkeypatch) -> None:
    calls = []

    def forward(worker_url, path, payload, *, token, timeout=None):
        calls.append((worker_url, token))
        if worker_url == "http://alpha:5000":
            return {"status": "error", "message": "worker_forward_failed", "http_status": 401}
        return {"data": {"reason": "beta answer"}}

    picks = [("http://alpha:5000", "stale"), ("http://beta:5000", "valid")]
    monkeypatch.setattr("agent.services.task_runtime_service.forward_to_worker", forward)
    monkeypatch.setattr(
        "agent.routes.snakes_worker_routing._pick_worker_for_ask",
        lambda **_kwargs: picks.pop(0),
    )

    answer, trace = _worker_propose("repo code", None, worker_picker=None)

    assert answer == "beta answer"
    assert calls == [("http://alpha:5000", "stale"), ("http://beta:5000", "valid")]
    assert trace["worker_failover"]["reason"] == "worker_auth_rejected"


def test_worker_propose_routes_from_explicit_original_question(monkeypatch) -> None:
    captured = {}

    def forward(_worker_url, _path, payload, *, token, timeout=None):
        captured.update(payload=payload, timeout=timeout)
        return {"data": {"reason": "fast answer"}}

    monkeypatch.setenv("ANANTA_AI_SNAKE_PROFILE_ROUTING", "true")
    monkeypatch.setattr("agent.services.task_runtime_service.forward_to_worker", forward)

    answer, trace = _worker_propose(
        "A huge grounded prompt containing code repository architecture",
        None,
        routing_task_kind=resolve_snake_routing_task_kind("Erkläre mir CodeCompass"),
        worker_picker=lambda: ("http://worker:5000", "token"),
    )

    assert answer == "fast answer"
    assert trace["routing_task_kind"] == "classification"
    assert captured["timeout"] == 90


def test_worker_executes_profile_routed_request_via_model_invocation(monkeypatch) -> None:
    calls = {}

    def invoke(prompt, tools, **kwargs):
        assert tools == []
        calls.update(prompt=prompt, routing_ctx=kwargs["routing_ctx"])
        return {"content": "profil routed answer", "tool_calls": [], "metadata": {}}

    monkeypatch.setattr(
        "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools", invoke,
    )
    monkeypatch.setattr(
        "agent.services.tiny_router.snake_shadow.observe_snake_candidate",
        lambda prompt, *, agent_config: "shadow_candidate_validated",
    )

    result = TaskExecutionService().propose_direct_step(
        TaskStepProposeRequest(
            prompt="repository question",
            provider="ananta_profile",
            routing_task_kind="repo_analysis",
        ),
        agent_cfg={"hub_direct_execution": {"enabled": False}},
        provider_urls={},
        openai_api_key=None,
        agent_name="worker",
    )

    assert calls["routing_ctx"].task_kind == "repo_analysis"
    assert calls["routing_ctx"].model_role == "coder"
    assert result["raw"] == "profil routed answer"


def test_worker_executes_profile_routed_tool_selection(monkeypatch) -> None:
    captured = {}

    def invoke_with_tools(prompt, tools, **kwargs):
        captured.update(prompt=prompt, tools=tools, routing_ctx=kwargs["routing_ctx"])
        return {
            "content": "",
            "tool_calls": [{"name": "read_file", "args": {"path": "agent/config.py"}}],
        }

    monkeypatch.setattr(
        "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
        invoke_with_tools,
    )
    monkeypatch.setattr(
        "agent.services.tiny_router.snake_shadow.observe_snake_candidate",
        lambda prompt, *, agent_config: "shadow_candidate_validated",
    )
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]
    result = TaskExecutionService().propose_direct_step(
        TaskStepProposeRequest(
            prompt="choose a repository tool",
            provider="ananta_profile",
            routing_task_kind="classification",
            routing_tools=tools,
        ),
        agent_cfg={"hub_direct_execution": {"enabled": False}},
        provider_urls={},
        openai_api_key=None,
        agent_name="worker",
    )

    assert captured["routing_ctx"].allow_cloud is False
    assert captured["tools"] == tools
    assert result["tool_calls"][0]["name"] == "read_file"


def test_profile_chat_preserves_worker_tool_calls(monkeypatch) -> None:
    def forward(_worker_url, _path, payload, *, token, timeout=None):
        assert payload["provider"] == "ananta_profile"
        assert payload["routing_tools"]
        assert token == "token"
        assert timeout is None
        return {"data": {"reason": "", "tool_calls": [
            {"id": "call-1", "name": "read_file", "args": {"path": "agent/config.py"}}
        ], "inference": {
            "profile_id": "local_lfm25_agentic_fast",
            "provider": "llamacpp",
            "model": "lfm2.5-2.6b-agentic-q8_0",
        }}}

    monkeypatch.setattr("agent.services.task_runtime_service.forward_to_worker", forward)
    response, trace = _worker_profile_chat(
        [{"role": "user", "content": "inspect config"}],
        task_kind="classification",
        tools=[{"type": "function", "function": {"name": "read_file"}}],
        worker_picker=lambda: ("http://worker:5000", "token"),
    )

    call = response["choices"][0]["message"]["tool_calls"][0]
    assert call["function"]["name"] == "read_file"
    assert '"agent/config.py"' in call["function"]["arguments"]
    assert trace["routing_source"] == "hub_snake_profile_policy"
    assert trace["inference"]["model"] == "lfm2.5-2.6b-agentic-q8_0"


def test_profile_chat_marks_empty_worker_response_as_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.services.task_runtime_service.forward_to_worker",
        lambda *_args, **_kwargs: {"data": {"reason": "", "tool_calls": []}},
    )

    response, trace = _worker_profile_chat(
        [{"role": "user", "content": "synthesize"}],
        task_kind="repo_analysis",
        worker_picker=lambda: ("http://worker:5000", "token"),
    )

    assert response is None
    assert trace["error"] == "empty_worker_response"


def test_snake_shadow_observation_is_forced_candidate_only(monkeypatch) -> None:
    captured = {}

    class Router:
        def route(self, **kwargs):
            captured.update(kwargs)
            return type("Decision", (), {"reason_code": "shadow_candidate_validated"})()

    monkeypatch.setattr(
        "agent.services.tiny_router.service.get_tiny_tool_router_service", lambda: Router(),
    )
    reason = observe_snake_candidate(
        "status",
        agent_config={
            "ananta_worker_tool_loop": {
                "allowed_tools": ["git.status"],
                "tiny_router": {"mode": "shadow", "profile_order": ["needle-2-45m"]},
            }
        },
    )

    assert reason == "shadow_candidate_validated"
    assert captured["config"]["mode"] == "shadow"
    assert captured["mutation_mode"] == "read_only"
