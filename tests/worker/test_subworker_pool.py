from worker.core.subworker_envelope import create_subworker_envelope
from worker.core.subworker_pool import SubworkerPool


def _ok_exec(envelope):
    return {"child_task_id": envelope.child_task_id, "ok": True}


def test_subworker_pool_runs_valid_subtask():
    env, errors = create_subworker_envelope(
        parent_execution_id="p1",
        child_task_id="c1",
        delegated_objective="analyze",
        parent_capabilities=["code_read", "subworker_spawn"],
        reduced_capabilities=["code_read"],
        context_subset_ref="ctx:1",
        audit_correlation_id="a1",
        max_children=2,
    )
    assert errors == []
    result = SubworkerPool(max_children_per_parent=2).run_subtask(env, execute_fn=_ok_exec)
    assert result.status == "success"


def test_subworker_pool_denies_capability_escalation():
    env, _ = create_subworker_envelope(
        parent_execution_id="p1",
        child_task_id="c2",
        delegated_objective="mutate",
        parent_capabilities=["code_read"],
        reduced_capabilities=["code_read", "shell_execute"],
        context_subset_ref="ctx:2",
        audit_correlation_id="a2",
    )
    result = SubworkerPool(max_children_per_parent=1).run_subtask(env, execute_fn=_ok_exec)
    assert result.status == "denied"
    assert "subworker_capability_escalation" in result.reason_code


def test_subworker_pool_requires_context_subset_ref():
    env, _ = create_subworker_envelope(
        parent_execution_id="p1",
        child_task_id="c3",
        delegated_objective="analyze",
        parent_capabilities=["code_read"],
        reduced_capabilities=["code_read"],
        context_subset_ref="",
        audit_correlation_id="a3",
    )
    result = SubworkerPool(max_children_per_parent=1).run_subtask(env, execute_fn=_ok_exec)
    assert result.status == "denied"

