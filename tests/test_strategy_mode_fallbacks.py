from worker.core.propose_orchestrator import ProposeContext, ProposeStrategyOrchestrator
from worker.core.propose import ProposeStrategyResult
from agent.services.propose_policy import ProposePolicy


class _Decline:
    def __init__(self, sid):
        self.sid = sid

    def run(self, _ctx):
        return ProposeStrategyResult.declined(self.sid, "declined")


def test_disabled_strategy_is_marked_in_attempts():
    policy = ProposePolicy(
        strategy_order=["tool_calling_llm", "json_schema_llm", "human_review"],
        allow_json_schema_fallback=False,
        on_all_strategies_declined="needs_review",
    )
    orch = ProposeStrategyOrchestrator(
        policy,
        {
            "tool_calling_llm": _Decline("tool_calling_llm"),
            "human_review": _Decline("human_review"),
        },
    )
    result = orch.run(ProposeContext(goal_id="g1", task_id="t1", task={}, base_prompt="x"))
    attempts = result.metadata.get("attempted_strategies", [])
    js = next((a for a in attempts if a["strategy_id"] == "json_schema_llm"), None)
    assert js is not None
    assert js["reason"] == "disabled_by_strategy_mode"
