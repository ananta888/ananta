"""WFG-009: worker routing must honor workflow step role, task_kind,
required_capabilities, and blueprint_role_hints.

These tests cover the routing path added to
``agent.routes.tasks.orchestration_policy.routing`` and do not require
any LLM workers to be online.
"""

from __future__ import annotations

from agent.routes.tasks.orchestration_policy.routing import (
    WORKFLOW_ROLE_TO_WORKER_ROLES,
    choose_worker_for_task,
)


def _worker(*, url: str, roles: list[str], caps: list[str], security: str = "medium") -> dict:
    return {
        "url": url,
        "name": url.rsplit(":", 1)[-1],
        "status": "online",
        "available_for_routing": True,
        "registration_validated": True,
        "current_load": 0,
        "execution_limits": {"max_parallel_tasks": 4},
        "security_level": security,
        "worker_roles": roles,
        "capabilities": caps,
    }


def test_workflow_role_mapping_covers_blueprint_roles() -> None:
    """Every blueprint workflow role used by the contract must map
    to a known set of worker roles. The default fallback is also
    pinned to keep routing deterministic when a blueprint introduces
    a new role we have not seen yet."""
    for role in [
        "product_owner",
        "planner",
        "scrum_master",
        "developer",
        "qa_verifier",
        "security_reviewer",
        "coordinator",
    ]:
        assert role in WORKFLOW_ROLE_TO_WORKER_ROLES
        targets = WORKFLOW_ROLE_TO_WORKER_ROLES[role]
        assert targets, f"workflow role {role!r} has empty target list"
    assert "default" in WORKFLOW_ROLE_TO_WORKER_ROLES


def test_choose_worker_routes_gate_review_to_scrum_master_role() -> None:
    """A gate_review step driven by the scrum_master workflow role
    must be routed to a reviewer or planner worker — never a coder."""
    scrum_worker = _worker(
        url="http://scrum:5000",
        roles=["reviewer", "planner"],
        caps=["review", "planning", "gate.review"],
    )
    coder_worker = _worker(
        url="http://coder:5000",
        roles=["coder"],
        caps=["coding", "implementation"],
    )
    task = {
        "task_kind": "gate_review",
        "worker_execution_context": {
            "workflow_step": {
                "step_id": "step-gate-1",
                "role": "scrum_master",
                "task_kind": "gate_review",
                "required_capabilities": ["gate.review"],
            }
        },
    }
    selection = choose_worker_for_task(task, [scrum_worker, coder_worker])
    assert selection.worker_url == "http://scrum:5000"
    assert "reviewer" in selection.matched_roles
    assert "coder" not in selection.matched_roles
    assert selection.strategy == "capability_quality_load_match"
    # Provenance is recorded.
    assert selection.workflow_step_id == "step-gate-1"
    assert selection.workflow_step_role == "scrum_master"
    assert selection.workflow_task_kind == "gate_review"
    assert selection.routing_origin == "workflow_role_mapping"
    assert any(r.startswith("workflow_step_role:") for r in selection.reasons)


def test_choose_worker_routes_coding_to_developer_role() -> None:
    """A coding step driven by the developer workflow role must
    pick a coder worker over a generic reviewer."""
    coder_worker = _worker(
        url="http://coder:5000",
        roles=["coder"],
        caps=["coding", "implementation"],
    )
    reviewer_worker = _worker(
        url="http://reviewer:5000",
        roles=["reviewer"],
        caps=["review"],
    )
    task = {
        "task_kind": "coding",
        "worker_execution_context": {
            "workflow_step": {
                "step_id": "step-impl-1",
                "role": "developer",
                "task_kind": "coding",
            }
        },
    }
    selection = choose_worker_for_task(task, [reviewer_worker, coder_worker])
    assert selection.worker_url == "http://coder:5000"
    assert selection.matched_roles == ["coder"]
    assert selection.workflow_step_role == "developer"


def test_choose_worker_blocks_when_required_capability_missing() -> None:
    """A workflow step with required_capabilities that no online
    worker can satisfy must NOT silently fall back to an unrelated
    worker. The selection must come back as workflow_blocked so the
    caller can surface a pending_with_reason or escalate to a
    human approval flow."""
    weak_coder = _worker(
        url="http://coder:5000",
        roles=["coder"],
        caps=["coding"],
    )
    task = {
        "task_kind": "security_review",
        "worker_execution_context": {
            "workflow_step": {
                "step_id": "step-sec-1",
                "role": "security_reviewer",
                "task_kind": "security_review",
                "required_capabilities": ["security.deep_audit"],
            }
        },
    }
    selection = choose_worker_for_task(task, [weak_coder])
    assert selection.worker_url is None
    assert selection.strategy == "workflow_blocked"
    assert selection.routing_origin == "workflow_blocked"
    assert "workflow_capability_not_satisfied" in selection.reasons
    assert selection.workflow_step_id == "step-sec-1"
    assert selection.workflow_step_role == "security_reviewer"


def test_choose_worker_persists_routing_provenance() -> None:
    """When a worker is selected through the workflow-aware path
    the selection must record workflow_step_id, workflow_step_role,
    workflow_task_kind, and routing_origin so the audit event can
    reference the workflow decision."""
    worker = _worker(
        url="http://tester:5000",
        roles=["tester"],
        caps=["testing"],
    )
    task = {
        "task_kind": "testing",
        "worker_execution_context": {
            "workflow_step": {
                "step_id": "step-qa-1",
                "role": "qa_verifier",
                "task_kind": "testing",
            }
        },
    }
    selection = choose_worker_for_task(task, [worker])
    assert selection.worker_url == "http://tester:5000"
    assert selection.workflow_step_id == "step-qa-1"
    assert selection.workflow_step_role == "qa_verifier"
    assert selection.workflow_task_kind == "testing"
    assert selection.routing_origin == "workflow_role_mapping"
    # Reasons list contains the workflow_step_id, role, and origin.
    joined = "|".join(selection.reasons)
    assert "workflow_step_id:step-qa-1" in joined
    assert "workflow_step_role:qa_verifier" in joined
    assert "routing_origin:workflow_role_mapping" in joined


def test_choose_worker_legacy_task_routes_without_workflow_provenance() -> None:
    """Tasks without a workflow_step block must continue to route
    via the existing task_kind/capability path and produce no
    workflow provenance (so the audit log stays clean for legacy
    traffic)."""
    planner = _worker(
        url="http://planner:5000",
        roles=["planner"],
        caps=["planning"],
    )
    task = {"task_kind": "planning"}
    selection = choose_worker_for_task(task, [planner])
    assert selection.worker_url == "http://planner:5000"
    assert selection.workflow_step_id is None
    assert selection.workflow_step_role is None
    assert selection.workflow_task_kind is None
    assert selection.routing_origin is None
    assert not any(r.startswith("workflow_step") for r in selection.reasons)


def test_choose_worker_accepts_explicit_workflow_step_argument() -> None:
    """Callers that have already resolved the workflow_step (e.g.
    the queue reconciliation service in WFG-013) may pass it
    directly via the workflow_step kwarg. It must override the
    task's worker_execution_context block."""
    coder = _worker(url="http://coder:5000", roles=["coder"], caps=["coding"])
    task = {
        "task_kind": "coding",
        "worker_execution_context": {
            "workflow_step": {
                "step_id": "stale",
                "role": "developer",
                "task_kind": "coding",
            }
        },
    }
    explicit = {
        "step_id": "step-fresh-1",
        "role": "developer",
        "task_kind": "coding",
        "required_capabilities": ["coding"],
    }
    selection = choose_worker_for_task(task, [coder], workflow_step=explicit)
    assert selection.worker_url == "http://coder:5000"
    assert selection.workflow_step_id == "step-fresh-1"
    assert selection.routing_origin == "workflow_role_mapping"
