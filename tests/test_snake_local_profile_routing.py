from agent.models import TaskStepProposeRequest
from agent.routes.snakes_chat_helpers import SnakeAskLimits
from agent.routes.snakes_worker_routing import (
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

    def forward(worker_url, path, payload, *, token):
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


def test_worker_executes_profile_routed_request_via_model_invocation(monkeypatch) -> None:
    calls = {}

    def invoke(prompt, **kwargs):
        calls.update(prompt=prompt, routing_ctx=kwargs["routing_ctx"])
        return "profil routed answer"

    monkeypatch.setattr(
        "agent.services.model_invocation_service.ModelInvocationService.invoke", invoke,
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
