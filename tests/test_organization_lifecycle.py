from __future__ import annotations

import pytest

from agent.services.organization_lifecycle_service import (
    OrganizationActivitySnapshot,
    OrganizationLifecycleService,
)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("draft", "validated"),
        ("draft", "archived"),
        ("validated", "active"),
        ("validated", "draft"),
        ("validated", "archived"),
        ("active", "paused"),
        ("active", "completed"),
        ("paused", "active"),
        ("paused", "completed"),
        ("paused", "archived"),
        ("completed", "archived"),
        ("archived", "validated"),
    ],
)
def test_declared_lifecycle_transitions_are_allowed(source: str, target: str) -> None:
    plan = OrganizationLifecycleService().plan_transition(
        organization_id="organization-a",
        current_state=source,
        target_state=target,
        activity=OrganizationActivitySnapshot(),
    )

    assert plan.allowed is True
    assert plan.starts_workers is False
    assert plan.reruns_tasks is False


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("draft", "active"),
        ("active", "archived"),
        ("completed", "active"),
        ("archived", "active"),
    ],
)
def test_undeclared_lifecycle_transitions_are_rejected(source: str, target: str) -> None:
    plan = OrganizationLifecycleService().plan_transition(
        organization_id="organization-a",
        current_state=source,
        target_state=target,
        activity=OrganizationActivitySnapshot(),
    )

    assert plan.allowed is False


def test_completion_with_active_work_requires_explicit_strategy() -> None:
    activity = OrganizationActivitySnapshot(
        running_task_ids=("task-a",),
        active_lease_ids=("lease-a",),
        open_gate_ids=("gate-a",),
        open_handoff_ids=("handoff-a",),
    )
    service = OrganizationLifecycleService()

    blocked = service.plan_transition(
        organization_id="organization-a",
        current_state="active",
        target_state="completed",
        activity=activity,
    )
    drained = service.plan_transition(
        organization_id="organization-a",
        current_state="active",
        target_state="completed",
        activity=activity,
        active_work_strategy="drain",
    )

    assert blocked.allowed is False
    assert blocked.reason_code == "organization_active_work_strategy_required"
    assert drained.allowed is True
    assert "drain_running_tasks" in drained.required_operations
    assert "resolve_open_handoffs" in drained.required_operations


def test_archived_recovery_preserves_lineage_without_automatic_execution() -> None:
    plan = OrganizationLifecycleService().plan_transition(
        organization_id="organization-a",
        current_state="archived",
        target_state="validated",
        activity=OrganizationActivitySnapshot(),
    )

    assert plan.allowed is True
    assert plan.reason_code == "organization_recovery_requires_new_activation"
    assert "create_new_activation_candidate" in plan.required_operations
    assert {"goals", "tasks", "assignments", "artifacts", "audit"}.issubset(plan.preserves_lineage)
    assert plan.starts_workers is False
    assert plan.reruns_tasks is False
