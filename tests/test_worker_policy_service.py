from __future__ import annotations

from types import SimpleNamespace

from agent.services.worker_policy_service import WorkerPolicyService


def test_worker_policy_filters_missing_capabilities():
    task = SimpleNamespace(required_capabilities=["coding"], worker_execution_context={})
    workers = [
        SimpleNamespace(url="http://w1:5000", capabilities=["planning"]),
        SimpleNamespace(url="http://w2:5000", capabilities=["coding", "planning"]),
    ]
    accepted, rejected = WorkerPolicyService().filter_candidates(
        task=task,
        workers=workers,
        policy_cfg={"enabled": True, "enforce_required_capabilities": True, "enforce_llm_scope": False},
    )
    assert len(accepted) == 1
    assert accepted[0].url == "http://w2:5000"
    assert any(item.get("reason_code") == "missing_capability" for item in rejected)


def test_worker_policy_filters_non_local_for_local_scope():
    task = SimpleNamespace(
        required_capabilities=[],
        worker_execution_context={"workspace_context_policy": {"llm_scope": "local_only"}},
    )
    workers = [
        SimpleNamespace(url="http://localhost:5000", capabilities=[]),
        SimpleNamespace(url="http://remote-worker:5000", capabilities=[]),
    ]
    accepted, rejected = WorkerPolicyService().filter_candidates(
        task=task,
        workers=workers,
        policy_cfg={"enabled": True, "enforce_required_capabilities": False, "enforce_llm_scope": True},
    )
    assert len(accepted) == 1
    assert accepted[0].url == "http://localhost:5000"
    assert any(item.get("reason_code") == "llm_scope_denied" for item in rejected)
