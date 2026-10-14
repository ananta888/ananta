"""Hub task entry point keeps passive/shadow/unsafe plans out of execution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.services.hub_multi_tool_pipeline import HubMultiToolPipeline
from ananta_contracts.multi_tool_plan import MultiToolPlan, ToolPlanStep


def plan(*names):
    return MultiToolPlan(
        "plan", "ready", tuple(ToolPlanStep(n, "call", "ready", enabled=True, confidence=0.99) for n in names)
    )


TASK = {
    "id": "task",
    "tenant_id": "tenant",
    "project_id": "project",
    "workspace_dir": "/workspace",
    "status": "running",
    "worker_execution_context": {"allowed_tools": ["a", "b"]},
}
CFG = {
    "hub_direct_execution": {
        "enabled": True,
        "allowed_tools": ["a", "b", "ungranted"],
        "multi_tool_plan": {"multi_tool_plan": True, "mode": "active"},
    }
}


class Router:
    def __init__(self, status="plan", shadow=False):
        self.status, self.shadow, self.seen = status, shadow, []

    def plan(self, **kw):
        self.seen.append(kw)
        return SimpleNamespace(status=self.status, shadow=self.shadow, plan=plan("a", "b"))


class Executor:
    def __init__(self):
        self.seen = []

    def execute(self, plan, **kw):
        self.seen.append(kw)
        return {"status": "completed"}


def test_exact_task_scope_reaches_existing_gateway():
    r, e = Router(), Executor()
    p = HubMultiToolPipeline(router=r, executor=e)
    assert p.try_execute(prompt="status", task=TASK, agent_cfg=CFG) == {"status": "completed"}
    assert r.seen[0]["allowed_tools"] == ("a", "b")
    assert e.seen[0]["scope"].tenant_id == "tenant"
    assert e.seen[0]["scope"].workspace_ref == "/workspace"


@pytest.mark.parametrize(
    "patch",
    [
        {"organization_id": "org"},
        {"derivation_reason": "goal_task_recovery"},
        {"tenant_id": ""},
        {"worker_execution_context": {}},
        {"workspace_dir": ""},
    ],
)
def test_other_assignment_bound_or_unscoped_paths_keep_existing_flow(patch):
    r, e = Router(), Executor()
    assert (
        HubMultiToolPipeline(router=r, executor=e).try_execute(prompt="status", task={**TASK, **patch}, agent_cfg=CFG)
        is None
    )
    assert not r.seen and not e.seen


@pytest.mark.parametrize("status,shadow", [("shadow_plan", True), ("escalate", False), ("disabled", False)])
def test_shadow_abstain_and_disabled_never_execute(status, shadow):
    r, e = Router(status, shadow), Executor()
    assert HubMultiToolPipeline(router=r, executor=e).try_execute(prompt="status", task=TASK, agent_cfg=CFG) is None
    assert not e.seen


def test_policy_block_does_not_become_llm_fallback():
    class Blocked(Executor):
        def execute(self, *args, **kw):
            return {"status": "blocked", "reason": "role_denied"}

    outcome = HubMultiToolPipeline(router=Router(), executor=Blocked()).try_execute(
        prompt="status", task=TASK, agent_cfg=CFG
    )
    assert outcome["status"] == "blocked"


def test_task_scope_change_after_scoring_is_fenced():
    class ExecutorWithFence(Executor):
        def execute(self, *args, **kw):
            assert kw["cancel_check"]()
            return {"status": "blocked"}

    p = HubMultiToolPipeline(
        router=Router(), executor=ExecutorWithFence(), task_lookup=lambda _: {**TASK, "tenant_id": "different"}
    )
    assert p.try_execute(prompt="status", task=TASK, agent_cfg=CFG)["status"] == "blocked"


def test_worker_or_missing_hub_context_cannot_compose_orchestration():
    from flask import Flask

    from agent.services.hub_multi_tool_pipeline import get_hub_multi_tool_pipeline

    with pytest.raises(ValueError, match="hub_context_required"):
        get_hub_multi_tool_pipeline()
    app = Flask(__name__)
    app.config["ROLE"] = "worker"
    with app.app_context(), pytest.raises(ValueError, match="hub_context_required"):
        get_hub_multi_tool_pipeline()
    assert "hub_multi_tool_pipeline" not in app.extensions
