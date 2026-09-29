from __future__ import annotations

from types import SimpleNamespace

from agent.routes.tasks.autopilot_dispatch_policy import resolve_target_worker_for_task
from agent.services.worker_policy_service import WorkerPolicyService


def test_worker_selection_decision_payload_fields_present():
    task = SimpleNamespace(
        id="t-1",
        assigned_agent_url=None,
        required_capabilities=["coding"],
        worker_execution_context={"workspace_context_policy": {"llm_scope": "local_only"}},
        _hub_can_be_worker=False,
        _local_worker_url="http://hub:5000",
    )
    workers = [
        SimpleNamespace(url="http://remote:5000", capabilities=["coding"]),
        SimpleNamespace(url="http://localhost:5000", capabilities=["planning"]),
    ]
    filtered, rejected = WorkerPolicyService().filter_candidates(
        task=task,
        workers=workers,
        policy_cfg={"enabled": True, "enforce_required_capabilities": True, "enforce_llm_scope": True},
    )
    target, _, was_assigned, reason = resolve_target_worker_for_task(task=task, workers=filtered, worker_cursor=0)
    payload = {
        "selected_worker": getattr(target, "url", None) if target is not None else None,
        "candidate_count": len(workers),
        "rejected_candidates": rejected,
        "reason_code": reason or ("assigned_worker" if not was_assigned else "round_robin"),
    }
    assert "selected_worker" in payload
    assert "candidate_count" in payload
    assert "rejected_candidates" in payload
    assert "reason_code" in payload
