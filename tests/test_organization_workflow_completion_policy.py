from __future__ import annotations

import copy
import time
from types import SimpleNamespace

import pytest

from agent.db_models import (
    ApprovalRequestDB,
    OrganizationInstanceDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    TaskDB,
    VerificationRecordDB,
)
from agent.repository import task_repo
from agent.services.organization_workflow_completion_policy_service import (
    ORGANIZATION_WORKFLOW_APPROVAL_REF_FIELD,
    ORGANIZATION_WORKFLOW_APPROVAL_REF_SCHEMA,
    ORGANIZATION_WORKFLOW_APPROVAL_TOOL,
    ORGANIZATION_WORKFLOW_WAITING_REASON,
    OrganizationWorkflowCompletionDecision,
    OrganizationWorkflowCompletionPolicyService,
)
from agent.services.task_runtime_service import update_local_task_status


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def one_or_none(self):
        if len(self._rows) > 1:
            raise AssertionError("expected at most one row")
        return self._rows[0] if self._rows else None

    def first(self):
        return self._rows[0] if self._rows else None


class _PolicySession:
    def __init__(
        self,
        *,
        organization,
        request,
        verification,
        assignments,
        slots,
    ):
        self.organization = organization
        self.request = request
        self.verification = verification
        self.assignments = {row.id: row for row in assignments}
        self.slots = {row.id: row for row in slots}

    def exec(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is OrganizationInstanceDB:
            return _Rows([self.organization] if self.organization else [])
        if entity is VerificationRecordDB:
            return _Rows([self.verification] if self.verification else [])
        raise AssertionError(f"unexpected query for {entity}")

    def get(self, entity, identifier):
        if entity is ApprovalRequestDB:
            return self.request if self.request and self.request.id == identifier else None
        if entity is OrganizationRoleAssignmentDB:
            return self.assignments.get(identifier)
        if entity is OrganizationRoleSlotDB:
            return self.slots.get(identifier)
        raise AssertionError(f"unexpected get for {entity}")


def _binding(*, revision: str = "d" * 64) -> dict:
    return {
        "schema": "organization_workflow_step_binding.v1",
        "organization_id": "organization-gate",
        "definition_revision": revision,
        "workflow_ref": "delivery_workflow@1",
        "workflow_content_hash": "w" * 64,
        "step_id": "implement",
        "team_unit_id": "unit-delivery",
        "team_id": "team-delivery",
        "role_slot_id": "slot-implementer",
        "gate": {
            "required": True,
            "acceptance_checks": ["tests_passed"],
            "approval_role_ref": "reviewer@1",
            "independent_principal_required": True,
        },
        "handoff_ref": None,
        "failure_policy": "manual",
    }


def _task(*, status: str, approval_request_id: str | None = None):
    binding = _binding()
    verification_status = {}
    if approval_request_id:
        verification_status[ORGANIZATION_WORKFLOW_APPROVAL_REF_FIELD] = {
            "schema": ORGANIZATION_WORKFLOW_APPROVAL_REF_SCHEMA,
            "approval_request_id": approval_request_id,
        }
    return SimpleNamespace(
        id="task-gate",
        tenant_id="tenant-gate",
        project_id="project-gate",
        organization_id="organization-gate",
        goal_id=None,
        unit_id="unit-delivery",
        team_id="team-delivery",
        role_slot_id="slot-implementer",
        status=status,
        status_reason_code=None,
        status_reason_details={},
        worker_execution_context={
            "organization_workflow_step_binding": copy.deepcopy(binding),
            "organization_routing": {"selected_assignment_id": "assignment-implementer"},
        },
        verification_spec={
            "acceptance_checks": ["tests_passed"],
            "approval_role_ref": "reviewer@1",
            "independent_principal_required": True,
            "failure_policy": "manual",
        },
        verification_status=verification_status,
    )


def _policy_fixture():
    policy = OrganizationWorkflowCompletionPolicyService()
    current = _task(status="in_progress")
    candidate = _task(
        status="completed",
        approval_request_id="approval-gate",
    )
    organization = SimpleNamespace(
        lifecycle="active",
        definition_revision="d" * 64,
    )
    implementer = SimpleNamespace(
        id="assignment-implementer",
        tenant_id="tenant-gate",
        project_id="project-gate",
        organization_id="organization-gate",
        role_slot_id="slot-implementer",
        lifecycle="active",
        assignment_metadata={"principal_id": "worker-principal"},
    )
    reviewer = SimpleNamespace(
        id="assignment-reviewer",
        tenant_id="tenant-gate",
        project_id="project-gate",
        organization_id="organization-gate",
        role_slot_id="slot-reviewer",
        lifecycle="active",
        assignment_metadata={"principal_id": "reviewer-principal"},
    )
    reviewer_slot = SimpleNamespace(
        id="slot-reviewer",
        tenant_id="tenant-gate",
        project_id="project-gate",
        organization_id="organization-gate",
        lifecycle="active",
        role_template_key="reviewer",
        role_template_version=1,
    )
    fingerprint = policy.binding_fingerprint(
        task=current,
        binding=_binding(),
    )
    request = SimpleNamespace(
        id="approval-gate",
        tenant_id="tenant-gate",
        project_id="project-gate",
        organization_id="organization-gate",
        task_id="task-gate",
        goal_id=None,
        status="granted",
        tool_name=ORGANIZATION_WORKFLOW_APPROVAL_TOOL,
        decided_by="reviewer-principal",
        decided_at=time.time() - 1,
        expires_at=time.time() + 3600,
        target_fingerprint=fingerprint,
        canonical_arguments=policy.approval_arguments(
            task=current,
            binding=_binding(),
            approval_assignment_id="assignment-reviewer",
        ),
    )
    verification = SimpleNamespace(
        id="verification-gate",
        task_id="task-gate",
        verification_type="quality_gate",
        status="passed",
        spec=copy.deepcopy(current.verification_spec),
        results={
            "quality_gates_passed": True,
            "final_passed": True,
        },
        updated_at=time.time(),
        created_at=time.time() - 1,
    )
    session = _PolicySession(
        organization=organization,
        request=request,
        verification=verification,
        assignments=[implementer, reviewer],
        slots=[reviewer_slot],
    )
    return policy, current, candidate, session


def test_completion_policy_accepts_only_revision_bound_active_role_approval():
    policy, current, candidate, session = _policy_fixture()

    decision = policy.evaluate(
        authoritative_task=current,
        candidate_task=candidate,
        session=session,
    )

    assert decision.allowed is True
    assert decision.approval_request_id == "approval-gate"


def test_completion_policy_rejects_grant_without_hub_verification_record():
    policy, current, candidate, session = _policy_fixture()
    session.verification = None

    decision = policy.evaluate(
        authoritative_task=current,
        candidate_task=candidate,
        session=session,
    )

    assert decision.allowed is False
    assert decision.reason_code == ("organization_workflow_gate_verification_missing")


@pytest.mark.parametrize(
    ("mutate", "reason_code"),
    [
        (
            lambda _current, _candidate, session: setattr(
                session.assignments["assignment-reviewer"],
                "lifecycle",
                "suspended",
            ),
            "organization_workflow_gate_approval_assignment_inactive",
        ),
        (
            lambda _current, _candidate, session: session.assignments["assignment-reviewer"].assignment_metadata.update(
                {"principal_id": "different-principal"}
            ),
            "organization_workflow_gate_approval_principal_mismatch",
        ),
        (
            lambda _current, _candidate, session: session.assignments[
                "assignment-implementer"
            ].assignment_metadata.update({"principal_id": "reviewer-principal"}),
            "organization_workflow_gate_independent_principal_required",
        ),
        (
            lambda _current, _candidate, session: setattr(
                session.organization,
                "definition_revision",
                "new-revision",
            ),
            "organization_workflow_gate_definition_drift",
        ),
        (
            lambda _current, _candidate, session: setattr(
                session.request,
                "target_fingerprint",
                "stale-workflow-fingerprint",
            ),
            "organization_workflow_gate_approval_workflow_drift",
        ),
        (
            lambda _current, _candidate, session: setattr(
                session.verification,
                "spec",
                {"acceptance_checks": ["different"]},
            ),
            "organization_workflow_gate_verification_spec_drift",
        ),
        (
            lambda _current, _candidate, session: session.verification.results.update({"quality_gates_passed": False}),
            "organization_workflow_gate_verification_not_passed",
        ),
    ],
)
def test_completion_policy_fails_closed_for_assignment_principal_and_drift(
    mutate,
    reason_code,
):
    policy, current, candidate, session = _policy_fixture()
    mutate(current, candidate, session)

    decision = policy.evaluate(
        authoritative_task=current,
        candidate_task=candidate,
        session=session,
    )

    assert decision.allowed is False
    assert decision.reason_code == reason_code


def _runtime_binding() -> dict:
    return _binding()


def _runtime_task(task_id: str) -> TaskDB:
    binding = _runtime_binding()
    return TaskDB(
        id=task_id,
        title="Required Organization gate",
        description="Wait for an authoritative role approval",
        status="assigned",
        worker_execution_context={
            "organization_workflow_step_binding": binding,
        },
        verification_spec={
            "acceptance_checks": binding["gate"]["acceptance_checks"],
            "approval_role_ref": binding["gate"]["approval_role_ref"],
            "independent_principal_required": True,
            "failure_policy": "manual",
        },
    )


def _deny_completion(monkeypatch):
    from agent.services import (
        organization_workflow_completion_policy_service as policy_module,
    )

    def deny(**_kwargs):
        return OrganizationWorkflowCompletionDecision(
            applicable=True,
            allowed=False,
            reason_code=ORGANIZATION_WORKFLOW_WAITING_REASON,
            binding_digest="b" * 64,
        )

    monkeypatch.setattr(
        policy_module.organization_workflow_completion_policy_service,
        "evaluate",
        deny,
    )


def test_local_completion_is_projected_to_waiting_for_review(monkeypatch):
    task_repo.save(_runtime_task("organization-gate-local"))
    _deny_completion(monkeypatch)

    update_local_task_status(
        "organization-gate-local",
        "completed",
        verification_status={"status": "passed"},
    )

    task = task_repo.get_by_id("organization-gate-local")
    assert task is not None
    assert task.status == "waiting_for_review"
    assert task.status_reason_code == ORGANIZATION_WORKFLOW_WAITING_REASON


def test_completed_ingress_with_required_binding_is_fail_closed():
    task = _runtime_task("organization-gate-ingress")
    task.status = "completed"

    persisted = task_repo.save(task)

    assert persisted.status == "waiting_for_review"
    assert persisted.status_reason_code == ORGANIZATION_WORKFLOW_WAITING_REASON


def test_patch_completion_returns_conflict_and_keeps_review_state(
    client,
    admin_auth_header,
    monkeypatch,
):
    task_repo.save(_runtime_task("organization-gate-patch"))
    _deny_completion(monkeypatch)

    response = client.patch(
        "/tasks/organization-gate-patch",
        headers=admin_auth_header,
        json={"status": "completed"},
    )

    assert response.status_code == 409
    assert response.get_json()["message"] == (ORGANIZATION_WORKFLOW_WAITING_REASON)
    assert task_repo.get_by_id("organization-gate-patch").status == ("waiting_for_review")


def test_orchestration_body_gate_cannot_bypass_required_approval(
    client,
    admin_auth_header,
    monkeypatch,
):
    task_repo.save(_runtime_task("organization-gate-orchestration"))
    _deny_completion(monkeypatch)

    response = client.post(
        "/tasks/orchestration/complete",
        headers=admin_auth_header,
        json={
            "task_id": "organization-gate-orchestration",
            "actor": "forged-reviewer",
            "gate_results": {"passed": True},
            "output": "worker execution finished",
        },
    )

    assert response.status_code == 409
    assert response.get_json()["message"] == (ORGANIZATION_WORKFLOW_WAITING_REASON)
    task = task_repo.get_by_id("organization-gate-orchestration")
    assert task is not None
    assert task.status == "waiting_for_review"
