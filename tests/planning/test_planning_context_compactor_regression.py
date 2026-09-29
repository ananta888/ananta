from worker.core.propose_orchestrator import ProposeContext


def test_propose_context_new_compaction_fields_are_optional():
    ctx = ProposeContext(goal_id="g", task_id="t", task={}, base_prompt="x")
    assert ctx.planning_context_compaction is None
    assert ctx.planning_context_compaction_meta is None


def test_propose_context_accepts_compaction_fields():
    ctx = ProposeContext(
        goal_id="g",
        task_id="t",
        task={},
        base_prompt="x",
        planning_context_compaction={"goal_summary": "short"},
        planning_context_compaction_meta={"status": "success"},
    )
    assert ctx.planning_context_compaction["goal_summary"] == "short"
    assert ctx.planning_context_compaction_meta["status"] == "success"
