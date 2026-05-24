from types import SimpleNamespace

from agent.services.task_scoped_execution_service import TaskScopedExecutionService


class _Req:
    def __init__(self):
        self.prompt = "Implement secure API"
        self.research_context = None
        self.strategy_mode = None

    def model_dump(self):
        return {"prompt": self.prompt}


def test_propose_uses_compactor_metadata(monkeypatch):
    svc = TaskScopedExecutionService()
    task = {"id": "t1", "goal_id": "g1", "description": "d", "status": "todo"}

    monkeypatch.setattr(svc, "_require_task", lambda tid: task)
    monkeypatch.setattr(svc, "_forward_task_request_if_remote", lambda **kwargs: None)
    monkeypatch.setattr(
        "agent.services.task_scoped_execution_service.get_goal_config_runtime_service",
        SimpleNamespace(get_effective_config=lambda **kwargs: SimpleNamespace(config={"propose_policy": {}}, source="test")),
    )
    monkeypatch.setattr(
        "agent.services.task_scoped_execution_service.get_research_context_bridge_service",
        lambda: SimpleNamespace(build_context=lambda **kwargs: {"prompt_section": "security policy"}),
    )
    monkeypatch.setattr(
        "agent.services.task_scoped_execution_service.get_propose_policy_service",
        lambda: SimpleNamespace(get_effective_policy=lambda **kwargs: SimpleNamespace(
            context_compaction_enabled=True,
            context_compaction_required=False,
            context_compactor_fail_open=False,
            allow_shell_execution=False,
            effective_strategy_mode=None,
        )),
    )
    monkeypatch.setattr(
        "agent.services.task_scoped_execution_service.get_planning_context_compactor_service",
        lambda: SimpleNamespace(compact=lambda **kwargs: SimpleNamespace(payload={"goal_summary": "x"}, meta={"status": "success", "reduction_ratio": 0.4})),
    )
    monkeypatch.setattr(
        "agent.services.task_scoped_execution_service.get_instruction_layer_service",
        lambda: SimpleNamespace(assemble_for_task=lambda **kwargs: {"instruction_stack": {}, "diagnostics": {}, "rendered_system_prompt": "x"}),
    )
    monkeypatch.setattr(
        "agent.services.task_scoped_execution_service.build_strategy_registry",
        lambda: {},
        raising=False,
    )

    class _Result:
        status = "advisory"
        reason = "r"
        metadata = {"attempted_strategies": [], "selected_strategy": None}
        proposal = None
        is_executable = False
        strategy_id = "x"

        def to_dict(self):
            return {"status": self.status}

    monkeypatch.setattr(
        "agent.services.task_scoped_execution_service.ProposeStrategyOrchestrator",
        lambda policy, strategies: SimpleNamespace(run=lambda context: _Result()),
        raising=False,
    )

    monkeypatch.setattr(
        "agent.services.task_scoped_execution_service.get_core_services",
        lambda: SimpleNamespace(task_execution_service=SimpleNamespace(persist_task_proposal_result=lambda **kwargs: None)),
    )

    out = svc.propose_task_step("t1", _Req(), cli_runner=lambda **kwargs: (0, "", "", "sgpt"), forwarder=lambda *a, **k: {}, tool_definitions_resolver=lambda *_: [])
    assert out.status == "success"
