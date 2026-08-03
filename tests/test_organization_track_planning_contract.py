from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agent.db_models import PlanningArtifactRevisionDB, TaskDB, WorkerJobDB
from agent.services.category_to_planning_track_service import (
    CategoryToPlanningTrackError,
    CategoryToPlanningTrackService,
    validate_authoritative_track_planning_assignment,
)
from agent.services.organization_track_planning_contract_service import (
    required_track_category_item_ids,
    track_planning_result_digest,
    validate_track_planning_result_carrier,
)
from agent.services.organization_track_planning_service import (
    OrganizationTrackPlanningService,
)
from agent.services.planning_artifact_transition_service import (
    PlanningTransitionError,
)
from agent.services.planning_category_contract_service import (
    stable_planning_digest,
)

_ROOT = Path(__file__).resolve().parents[1]
_RESULT_SCHEMA = _ROOT / "schemas" / "worker" / "organization_track_planning_result.v1.json"


def _carrier() -> dict:
    carrier = {
        "schema": "organization_track_planning_result.v1",
        "payload_digest": "",
        "category_revision_id": "category-r1",
        "source_category_item_ids": ["ITEM-A", "ITEM-B"],
        "track_candidates": [
            {
                "artifact_id": "track-a",
                "payload": {
                    "version": "1.0.0",
                    "owner": "worker:planner-a",
                    "track": "delivery",
                    "status_scale": ["todo", "done"],
                    "priority_scale": ["P1"],
                    "risk_scale": ["low"],
                    "milestones": [],
                    "tasks": [],
                    "tasks_status_summary": {},
                    "source_category_item_ids": ["ITEM-A"],
                },
            }
        ],
        "exclusions": {"ITEM-B": "Deferred by the explicit Track plan."},
    }
    carrier["payload_digest"] = track_planning_result_digest(carrier)
    return carrier


def test_track_planning_result_schema_and_runtime_validator_are_closed() -> None:
    schema = json.loads(_RESULT_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    carrier = _carrier()

    assert list(Draft202012Validator(schema).iter_errors(carrier)) == []
    assert validate_track_planning_result_carrier(carrier) == carrier

    open_carrier = {**carrier, "requested_worker_id": "worker-attacker"}
    assert list(Draft202012Validator(schema).iter_errors(open_carrier))
    with pytest.raises(
        PlanningTransitionError,
        match="track_planning_result_carrier_invalid",
    ):
        validate_track_planning_result_carrier(open_carrier)


def test_track_planning_result_digest_binds_candidates_and_category_scope() -> None:
    carrier = _carrier()
    tampered = copy.deepcopy(carrier)
    tampered["track_candidates"][0]["payload"]["track"] = "attacker-track"

    assert track_planning_result_digest(tampered) != carrier["payload_digest"]
    with pytest.raises(
        PlanningTransitionError,
        match="track_planning_result_digest_mismatch",
    ):
        validate_track_planning_result_carrier(tampered)


def test_track_planning_scope_is_all_and_only_non_deferred_category_items() -> None:
    payload = {
        "categories": [
            {
                "items": [
                    {"id": "ITEM-B", "status": "open"},
                    {"id": "ITEM-A", "status": "in_progress"},
                    {"id": "ITEM-Z", "status": "deferred"},
                ]
            }
        ]
    }

    assert required_track_category_item_ids(payload) == ("ITEM-A", "ITEM-B")


def test_track_planning_scope_rejects_duplicate_category_item_ids() -> None:
    payload = {
        "categories": [
            {
                "items": [
                    {"id": "ITEM-A", "status": "open"},
                    {"id": "ITEM-A", "status": "deferred"},
                ]
            }
        ]
    }

    with pytest.raises(
        PlanningTransitionError,
        match="track_planning_category_item_id_duplicate",
    ):
        required_track_category_item_ids(payload)


def test_track_result_is_forwarded_to_authoritative_task_and_lease_guard() -> None:
    task = TaskDB(
        id="planning-task-1",
        task_kind="planning_track_task",
        worker_execution_context={
            "planning_track_binding": {
                "category_revision_id": "category-r1",
                "category_digest": "a" * 64,
                "policy_hash": "b" * 64,
                "prompt_hash": "c" * 64,
                "source_category_item_ids": ["ITEM-A", "ITEM-B"],
            }
        },
    )

    class FakeSession:
        def get(self, model, key):
            assert model is TaskDB
            return task if key == task.id else None

    class FakeUnitOfWork:
        def __init__(self) -> None:
            self.session = FakeSession()

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
            return None

    class FakeDerivation:
        def __init__(self) -> None:
            self.arguments: dict = {}

        def derive_tracks(self, **kwargs):
            self.arguments = kwargs
            return {
                "category_revision_id": kwargs["category_revision_id"],
                "track_revisions": [],
                "excluded_category_items": dict(kwargs["exclusions"]),
                "materialized_task_ids": [],
                "replayed": False,
            }

    derivation = FakeDerivation()
    service = OrganizationTrackPlanningService(
        track_derivation_service=derivation,
        uow_factory=FakeUnitOfWork,
    )
    carrier = _carrier()
    result = service.accept_result(
        source_task_id=task.id,
        assignment_id="assignment-1",
        capability_claims={
            "source_task_id": task.id,
            "assignment_id": "assignment-1",
            "dispatch_lease_id": "lease-1",
            "worker_id": "worker-1",
            "scopes": ["worker.result.submit"],
        },
        carrier=carrier,
        idempotency_key="transport-key-1",
    )

    assert derivation.arguments["require_authoritative_task"] is True
    assert derivation.arguments["source_task_id"] == task.id
    assert derivation.arguments["assignment_id"] == "assignment-1"
    assert derivation.arguments["dispatch_lease_id"] == "lease-1"
    assert derivation.arguments["idempotency_key"] == ("planning-track-result:planning-task-1")
    assert derivation.arguments["result_payload_digest"] == carrier["payload_digest"]
    assert result["task_created"] is False
    assert result["queue_write"] is False


def test_authoritative_track_write_guard_rechecks_current_worker_job() -> None:
    category_payload = {"categories": [{"items": [{"id": "ITEM-A", "status": "open"}]}]}
    category = PlanningArtifactRevisionDB(
        id="category-r1",
        artifact_id="category-a",
        revision=1,
        artifact_type="planning_category_todo",
        tenant_id="tenant-1",
        project_id="project-1",
        organization_id="org-1",
        goal_id="goal-1",
        status="promoted",
        payload=category_payload,
        content_digest=stable_planning_digest(category_payload),
        schema_ref="todos/todo.schema.json",
        schema_hash="category-schema-hash",
        policy_hash="policy-hash",
    )
    task = TaskDB(
        id="planning-task-1",
        task_kind="planning_track_task",
        status="in_progress",
        tenant_id=category.tenant_id,
        project_id=category.project_id,
        organization_id=category.organization_id,
        goal_id=category.goal_id,
        unit_id="unit-1",
        team_id="team-1",
        role_slot_id="slot-1",
        current_worker_job_id="lease-1",
        worker_execution_context={
            "planning_track_binding": {
                "schema": "organization_track_planning_binding.v1",
                "category_revision_id": category.id,
                "category_revision": category.revision,
                "category_digest": category.content_digest,
                "category_schema_hash": category.schema_hash,
                "policy_hash": category.policy_hash,
                "prompt_hash": "prompt-hash",
                "prompt_template_ref": ("prompts/planning/organization_track_planning.j2"),
                "result_schema": "organization_track_planning_result.v1",
                "result_payload_schema_ref": "todos/todo.track.schema.json",
                "result_digest_algorithm": "sha256-canonical-json-v1",
                "organization_id": category.organization_id,
                "goal_id": category.goal_id,
                "unit_id": "unit-1",
                "team_id": "team-1",
                "role_slot_id": "slot-1",
                "source_catalog_id": None,
                "source_catalog_hash": None,
                "allowed_source_refs": [],
                "allowed_run_refs": [],
                "source_category_item_ids": ["ITEM-A"],
                "source_category_todo": category_payload,
                "worker_authority_ceiling": {
                    "allowed_task_capabilities": [],
                    "allowed_tools": [],
                    "allowed_context_refs": [],
                    "worker_controls_routing": False,
                    "worker_controls_budget": False,
                },
            }
        },
    )
    job = WorkerJobDB(
        id="lease-1",
        parent_task_id=task.id,
        subtask_id="assignment-1",
        worker_url="worker-1",
        status="delegated",
    )

    class FakeSession:
        def get(self, model, key):
            return {
                (TaskDB, task.id): task,
                (WorkerJobDB, job.id): job,
            }.get((model, key))

    guarded_task, guarded_job, _binding = validate_authoritative_track_planning_assignment(
        FakeSession(),
        category=category,
        source_task_id=task.id,
        assignment_id=job.subtask_id,
        dispatch_lease_id=job.id,
        worker_id=job.worker_url,
        required_source_category_item_ids=["ITEM-A"],
        prompt_hash="prompt-hash",
        result_payload_digest="sha256:" + "d" * 64,
    )
    assert guarded_task is task
    assert guarded_job is job

    with pytest.raises(
        CategoryToPlanningTrackError,
        match="track_planning_assignment_invalid",
    ):
        validate_authoritative_track_planning_assignment(
            FakeSession(),
            category=category,
            source_task_id=task.id,
            assignment_id=job.subtask_id,
            dispatch_lease_id=job.id,
            worker_id="worker-attacker",
            required_source_category_item_ids=["ITEM-A"],
            prompt_hash="prompt-hash",
            result_payload_digest="sha256:" + "d" * 64,
        )


def test_worker_track_candidate_cannot_claim_routing_tools_or_budget() -> None:
    issues = CategoryToPlanningTrackService._worker_authority_issues(
        artifact_id="track-a",
        payload={
            "tasks": [
                {
                    "id": "T01",
                    "team_id": "team-attacker",
                    "required_capabilities": ["admin"],
                    "allowed_tools": ["shell"],
                    "budget_estimate": {"estimated_tokens": 1_000_000},
                    "context_refs": ["SRC_9999"],
                    "description": "Unsupported claim from RUN_9999.",
                }
            ]
        },
        authority_ceiling={
            "allowed_task_capabilities": [],
            "allowed_context_refs": ["SRC_0001"],
        },
    )

    assert any("worker_authority_expansion:payload.tasks[0].team_id" in row for row in issues)
    assert any("worker_capability_expansion" in row for row in issues)
    assert any("worker_context_expansion" in row for row in issues)
    assert any("worker_grounding_ref_unknown" in row for row in issues)
