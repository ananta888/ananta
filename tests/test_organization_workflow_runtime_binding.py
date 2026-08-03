from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.db_models import PlanningTaskMappingDB, TaskDB
from agent.db_models.organizations import (
    OrganizationInstanceDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationUnitDB,
)
from agent.models.organization_models import canonical_definition_sha256
from agent.services.organization_reference_workflow_service import (
    OrganizationReferenceWorkflowError,
    OrganizationReferenceWorkflowService,
)
from agent.services.organization_workflow_task_binding_service import (
    OrganizationWorkflowTaskBindingService,
)
from agent.services.planning_artifact_transition_service import PlanningTransitionError
from agent.services.planning_task_materialization_service import (
    PlanningTaskMaterializationService,
)


class _Rows:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)

    def first(self):
        return self._values[0] if self._values else None


class _Session:
    def __init__(self, rows_by_entity):
        self._rows_by_entity = rows_by_entity

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def exec(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        return _Rows(self._rows_by_entity.get(entity, ()))


class _Catalog:
    def __init__(self, workflow):
        self.workflow = workflow

    def get_workflow_definition(self, key, version):
        return self.workflow if (key, version) == ("delivery_workflow", 1) else None


def _workflow() -> dict:
    selector = {
        "team_blueprint_ref": "delivery_team@1",
        "cardinality": 1,
        "routing": "single",
    }
    no_gate = {
        "required": False,
        "acceptance_checks": [],
        "approval_role_ref": None,
        "independent_principal_required": False,
    }
    return {
        "key": "delivery_workflow",
        "version": 1,
        "mode": "gated",
        "default_failure_policy": "block",
        "steps": [
            {
                "step_id": "implement",
                "title": "Implement the slice",
                "task_kind": "coding",
                "owner_role_ref": "developer@1",
                "target_team_selector": selector,
                "depends_on": [],
                "inputs": ["accepted_requirements"],
                "outputs": ["verified_increment"],
                "handoff_ref": "delivery_handoff@1",
                "gate": {
                    "required": True,
                    "acceptance_checks": ["tests_passed"],
                    "approval_role_ref": "reviewer@1",
                    "independent_principal_required": True,
                },
                "failure_policy": "manual",
            },
            {
                "step_id": "verify",
                "title": "Verify the slice",
                "task_kind": "testing",
                "owner_role_ref": "developer@1",
                "target_team_selector": selector,
                "depends_on": ["implement"],
                "inputs": ["verified_increment"],
                "outputs": ["verification_report"],
                "gate": no_gate,
                "failure_policy": "block",
            },
            {
                "step_id": "document",
                "title": "Document the result",
                "task_kind": "documentation",
                "owner_role_ref": "developer@1",
                "target_team_selector": selector,
                "depends_on": ["verify"],
                "inputs": ["verification_report"],
                "outputs": ["delivery_record"],
                "gate": no_gate,
                "failure_policy": "block",
            },
            {
                "step_id": "handoff",
                "title": "Prepare the handoff",
                "task_kind": "handoff",
                "owner_role_ref": "developer@1",
                "target_team_selector": selector,
                "depends_on": ["document"],
                "inputs": ["delivery_record"],
                "outputs": ["handoff_record"],
                "gate": no_gate,
                "failure_policy": "block",
            },
            {
                "step_id": "release",
                "title": "Release the result",
                "task_kind": "release",
                "owner_role_ref": "developer@1",
                "target_team_selector": selector,
                "depends_on": ["handoff"],
                "inputs": ["handoff_record"],
                "outputs": ["release_record"],
                "gate": no_gate,
                "failure_policy": "block",
            },
        ],
    }


def _preview(
    *,
    target_unit_id: str | None = None,
    ambiguous: bool = False,
) -> dict:
    organization = OrganizationInstanceDB(
        organization_id="organization-1",
        tenant_id="tenant-1",
        project_id="project-1",
        name="Company",
        definition_key="company",
        definition_version=1,
        definition_revision="d" * 64,
        lifecycle="active",
        effective_limit_profile_ref="limits@1",
        effective_limit_profile_revision=1,
        effective_limit_profile_hash="l" * 64,
        composition_mode="custom",
        plan_digest="p" * 64,
        idempotency_key="create-1",
    )
    unit = OrganizationUnitDB(
        id="unit-delivery",
        tenant_id="tenant-1",
        project_id="project-1",
        organization_id="organization-1",
        unit_key="delivery",
        name="Delivery",
        unit_kind="team",
        team_blueprint_key="delivery_team",
        team_blueprint_version=1,
        lifecycle="active",
    )
    link = OrganizationTeamLinkDB(
        id="link-delivery",
        tenant_id="tenant-1",
        project_id="project-1",
        organization_id="organization-1",
        unit_id=unit.id,
        team_id="team-delivery",
        lifecycle="active",
    )
    slot = OrganizationRoleSlotDB(
        id="slot-developer",
        tenant_id="tenant-1",
        project_id="project-1",
        organization_id="organization-1",
        unit_id=unit.id,
        slot_key="developer",
        role_template_key="developer",
        role_template_version=1,
        required=True,
        min_count=1,
        default_count=1,
        max_count=1,
        assignment_policy={"required_capabilities": ["coding"]},
        lifecycle="active",
    )
    units = [unit]
    links = [link]
    slots = [slot]
    if ambiguous:
        second_unit = OrganizationUnitDB(
            id="unit-delivery-2",
            tenant_id="tenant-1",
            project_id="project-1",
            organization_id="organization-1",
            unit_key="delivery-2",
            name="Delivery 2",
            unit_kind="team",
            team_blueprint_key="delivery_team",
            team_blueprint_version=1,
            lifecycle="active",
        )
        units.append(second_unit)
        links.append(
            OrganizationTeamLinkDB(
                id="link-delivery-2",
                tenant_id="tenant-1",
                project_id="project-1",
                organization_id="organization-1",
                unit_id=second_unit.id,
                team_id="team-delivery-2",
                lifecycle="active",
            )
        )
        slots.append(
            OrganizationRoleSlotDB(
                id="slot-developer-2",
                tenant_id="tenant-1",
                project_id="project-1",
                organization_id="organization-1",
                unit_id=second_unit.id,
                slot_key="developer",
                role_template_key="developer",
                role_template_version=1,
                required=True,
                min_count=1,
                default_count=1,
                max_count=1,
                assignment_policy={"required_capabilities": ["coding"]},
                lifecycle="active",
            )
        )
    workflow = _workflow()
    if ambiguous:
        workflow["targeting_policy"] = "explicit_unit_when_ambiguous"
    service = OrganizationReferenceWorkflowService(
        catalog=_Catalog(workflow),
        session_factory=lambda: _Session(
            {
                OrganizationInstanceDB: [organization],
                OrganizationUnitDB: units,
                OrganizationTeamLinkDB: links,
                OrganizationRoleSlotDB: slots,
            }
        ),
    )
    return service.preview_track_candidate(
        tenant_id="tenant-1",
        project_id="project-1",
        organization_id="organization-1",
        workflow_key="delivery_workflow",
        workflow_version=1,
        goal="Deliver a verified slice",
        source_category_item_ids=["CAT-001"],
        owner="hub:test",
        target_unit_id=target_unit_id,
    )


def test_reference_workflow_task_has_revision_gate_and_handoff_binding() -> None:
    preview = _preview()
    task = preview["payload"]["tasks"][0]

    assert task["organization_workflow_step_binding"] == {
        "schema": "organization_workflow_step_binding.v1",
        "organization_id": "organization-1",
        "definition_revision": "d" * 64,
        "workflow_ref": "delivery_workflow@1",
        "workflow_content_hash": canonical_definition_sha256(_workflow()),
        "step_id": "implement",
        "team_unit_id": "unit-delivery",
        "team_id": "team-delivery",
        "role_slot_id": "slot-developer",
        "gate": {
            "required": True,
            "acceptance_checks": ["tests_passed"],
            "approval_role_ref": "reviewer@1",
            "independent_principal_required": True,
        },
        "handoff_ref": "delivery_handoff@1",
        "failure_policy": "manual",
    }


def test_ambiguous_delivery_workflow_requires_an_explicit_team_unit() -> None:
    with pytest.raises(
        OrganizationReferenceWorkflowError,
        match="organization_reference_workflow_unroutable",
    ) as error:
        _preview(ambiguous=True)

    assert {blocker["reason_code"] for blocker in error.value.details["blockers"]} == {
        "ORGANIZATION_WORKFLOW_TARGET_UNIT_REQUIRED"
    }


def test_explicit_team_unit_routes_every_step_to_the_selected_delivery_cell() -> None:
    preview = _preview(
        ambiguous=True,
        target_unit_id="unit-delivery-2",
    )

    assert preview["task_count"] == len(_workflow()["steps"])
    assert preview["artifact_id"].endswith(":unit-delivery-2")
    assert {task["organization_binding"]["unit_id"] for task in preview["payload"]["tasks"]} == {"unit-delivery-2"}


def test_materialization_contract_copies_verification_and_rejects_replay_drift() -> None:
    payload = _preview()["payload"]
    plan_task = payload["tasks"][0]
    track = SimpleNamespace(
        organization_id="organization-1",
        payload=payload,
    )
    binding_service = OrganizationWorkflowTaskBindingService()
    binding = binding_service.workflow_step_binding(
        track=track,
        task=plan_task,
        current_definition_revision="d" * 64,
    )
    verification_spec = binding_service.verification_spec(plan_task)
    runtime_contract = {
        "workflow_binding": binding,
        "verification_spec": verification_spec,
    }
    mapping = PlanningTaskMappingDB(
        tenant_id="tenant-1",
        project_id="project-1",
        organization_id="organization-1",
        goal_id="organization-goal",
        execution_goal_id="team-goal",
        category_revision_id="category-revision",
        track_revision_id="track-revision",
        plan_task_id=plan_task["id"],
        internal_task_id="runtime-task",
        unit_id="unit-delivery",
        team_id="team-delivery",
        role_slot_id="slot-developer",
        materialization_receipt_id="receipt",
    )
    runtime_task = TaskDB(
        id="runtime-task",
        tenant_id="tenant-1",
        project_id="project-1",
        organization_id="organization-1",
        goal_id="team-goal",
        unit_id="unit-delivery",
        team_id="team-delivery",
        role_slot_id="slot-developer",
        worker_execution_context={"organization_workflow_step_binding": binding},
        verification_spec=verification_spec,
    )

    PlanningTaskMaterializationService._verify_existing_task(
        runtime_task,
        mapping,
        runtime_contract=runtime_contract,
    )
    runtime_task.verification_spec = {**verification_spec, "acceptance_checks": ["different"]}

    with pytest.raises(
        PlanningTransitionError,
        match="planning_materialized_task_runtime_contract_conflict",
    ):
        PlanningTaskMaterializationService._verify_existing_task(
            runtime_task,
            mapping,
            runtime_contract=runtime_contract,
        )


def test_materialization_rejects_stale_organization_definition_revision() -> None:
    payload = _preview()["payload"]
    task = payload["tasks"][0]
    track = SimpleNamespace(organization_id="organization-1", payload=payload)

    with pytest.raises(PlanningTransitionError, match="planning_task_workflow_binding_conflict"):
        OrganizationWorkflowTaskBindingService().workflow_step_binding(
            track=track,
            task=task,
            current_definition_revision="new-revision",
        )
