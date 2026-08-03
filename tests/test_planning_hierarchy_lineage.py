from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agent.db_models import (
    PlanningArtifactRevisionDB,
    PlanningLineageDB,
    PlanningTaskMappingDB,
    TaskDB,
    WorkerTaskProposalDB,
)
from agent.services.planning_artifact_transition_service import (
    PlanningOperationContext,
    PlanningTransitionError,
)
from agent.services.planning_category_contract_service import stable_planning_digest
from agent.services.planning_replan_service import PlanningReplanService
from agent.services.planning_status_projection_service import PlanningStatusProjectionService

TRACK_FIXTURE = Path(__file__).resolve().parent / "fixtures/planning_tracks/small_track.json"


class _SessionFake:
    def __init__(self, tasks: dict[str, TaskDB]) -> None:
        self.tasks = tasks

    def get(self, _model, object_id: str):
        return self.tasks.get(object_id)


class _PlanningRepositoryFake:
    def __init__(
        self,
        *,
        revisions: list[PlanningArtifactRevisionDB],
        mappings: list[PlanningTaskMappingDB],
        lineage: list[PlanningLineageDB],
        proposals: list[WorkerTaskProposalDB] | None = None,
    ) -> None:
        self.revisions = revisions
        self.mappings = mappings
        self.lineage = lineage
        self.proposals = list(proposals or [])

    def acquire_scope_lock(self, _scope_key: str) -> None:
        return None

    def get_revision(self, revision_id: str, *, for_update: bool = False):
        del for_update
        return next((row for row in self.revisions if row.id == revision_id), None)

    def list_revisions(self, *, goal_id: str, organization_id: str, artifact_type: str | None = None):
        return [
            row
            for row in self.revisions
            if row.goal_id == goal_id
            and row.organization_id == organization_id
            and (artifact_type is None or row.artifact_type == artifact_type)
        ]

    def list_mappings(self, track_revision_id: str):
        return [row for row in self.mappings if row.track_revision_id == track_revision_id]

    def list_proposals(self, *, organization_id: str, source_goal_id: str | None = None, state=None):
        return [
            row
            for row in self.proposals
            if row.organization_id == organization_id
            and (source_goal_id is None or row.source_goal_id == source_goal_id)
            and (state is None or row.state == state)
        ]

    def list_amendment_inputs(self, *, organization_id: str, goal_id: str):
        del organization_id, goal_id
        return []

    def list_lineage_for_track(self, track_revision_id: str):
        return [row for row in self.lineage if row.track_revision_id == track_revision_id]

    def find_mappings_for_plan_task(self, *, goal_id: str, plan_task_id: str):
        return [row for row in self.mappings if row.goal_id == goal_id and row.plan_task_id == plan_task_id]

    def next_revision_number(self, *, artifact_id: str) -> int:
        return 1 + max(
            (row.revision for row in self.revisions if row.artifact_id == artifact_id),
            default=0,
        )

    def add_revision(self, revision: PlanningArtifactRevisionDB):
        self.revisions.append(revision)
        return revision

    def add_lineage(self, rows) -> None:
        self.lineage.extend(rows)


class _UnitOfWorkFake:
    def __init__(self, repository: _PlanningRepositoryFake, session: _SessionFake) -> None:
        self.planning = repository
        self.session = session

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        return None


def _category() -> PlanningArtifactRevisionDB:
    payload = {
        "project": "organization-a",
        "categories": [
            {
                "name": "delivery",
                "label": "Delivery",
                "items": [
                    {"id": "ITEM-A", "title": "Analyze", "status": "open"},
                    {"id": "ITEM-B", "title": "Deliver", "status": "open"},
                ],
            }
        ],
        "meta": {
            "total_items": 999,
            "by_status": {"completed": 999, "partial": 0, "open": 0},
            "notes": [],
            "recommended_order": ["ITEM-A", "ITEM-B"],
        },
    }
    return PlanningArtifactRevisionDB(
        id="category-r1",
        artifact_id="category-artifact",
        revision=1,
        artifact_type="planning_category_todo",
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        goal_id="goal-a",
        status="promoted",
        payload=payload,
        content_digest=stable_planning_digest(payload),
        schema_ref="todos/todo.schema.json",
        schema_hash="category-schema",
        policy_hash="policy-a",
        validation_result={"valid": True, "promotable": True},
    )


def _track(category: PlanningArtifactRevisionDB) -> PlanningArtifactRevisionDB:
    payload = json.loads(TRACK_FIXTURE.read_text(encoding="utf-8"))
    payload["source_category_item_ids"] = ["ITEM-A", "ITEM-B"]
    payload["tasks"][0]["source_category_item_ids"] = ["ITEM-A"]
    payload["tasks"][0]["depends_on"] = []
    payload["tasks"][1]["source_category_item_ids"] = ["ITEM-B"]
    payload["tasks"][1]["depends_on"] = ["T01"]
    payload["tasks"][2]["source_category_item_ids"] = ["ITEM-B"]
    payload["tasks"][2]["depends_on"] = ["T02"]
    return PlanningArtifactRevisionDB(
        id="track-r1",
        artifact_id="track-artifact",
        revision=1,
        artifact_type="planning_track",
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        goal_id="goal-a",
        status="adopted",
        payload=payload,
        content_digest=stable_planning_digest(payload),
        schema_ref="todos/todo.track.schema.json",
        schema_hash="track-schema",
        policy_hash="policy-a",
        source_category_item_ids=["ITEM-A", "ITEM-B"],
        execution_provenance={"source_category_digest": category.content_digest},
        validation_result={"valid": True},
        parent_revision_id=category.id,
    )


def _mapping(track: PlanningArtifactRevisionDB, plan_task_id: str, task_id: str, item_id: str):
    return PlanningTaskMappingDB(
        id=f"mapping-{plan_task_id}",
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        goal_id="goal-a",
        execution_goal_id="team-goal-a",
        category_revision_id="category-r1",
        track_revision_id=track.id,
        source_category_item_ids=[item_id],
        plan_task_id=plan_task_id,
        internal_task_id=task_id,
        materialization_receipt_id="materialization-receipt-a",
    )


def _lineage(track: PlanningArtifactRevisionDB) -> list[PlanningLineageDB]:
    return [
        PlanningLineageDB(
            id=f"lineage-{task_id}",
            tenant_id="tenant-a",
            project_id="project-a",
            organization_id="organization-a",
            goal_id="goal-a",
            category_revision_id="category-r1",
            track_revision_id=track.id,
            source_category_item_id=item_id,
            plan_task_id=task_id,
        )
        for task_id, item_id in (("T01", "ITEM-A"), ("T02", "ITEM-B"), ("T03", "ITEM-B"))
    ]


def _proposal() -> WorkerTaskProposalDB:
    return WorkerTaskProposalDB(
        proposal_id="proposal-a",
        idempotency_key="proposal-a-key",
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        source_goal_id="goal-a",
        source_task_id="runtime-t02",
        unit_id="unit-a",
        team_id="team-a",
        role_slot_id="slot-a",
        assignment_id="assignment-a",
        dispatch_lease_id="lease-a",
        proposing_role_template_ref="developer@1",
        proposing_worker_id="worker-a",
        role_template_version="1",
        payload_digest="sha256:" + "1" * 64,
        envelope_digest="sha256:" + "2" * 64,
        policy_hash="sha256:" + "3" * 64,
        envelope={},
        source_category_item_ids=["ITEM-B"],
        state="needs_approval",
        approval_request_id="approval-a",
        decision={
            "category_artifact_revision_id": "category-r1",
            "category_revision": 1,
            "category_digest": "category-digest-exact",
            "source_track_artifact_revision_id": "track-r1",
            "source_track_revision": 1,
            "source_track_digest": "track-digest-exact",
        },
    )


def _context(*, organization_id: str = "organization-a") -> PlanningOperationContext:
    return PlanningOperationContext.hub_admin(
        subject_id="operator-a",
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id=organization_id,
    )


def test_projection_recomputes_task_track_category_and_goal_status_from_runtime_truth() -> None:
    category = _category()
    track = _track(category)
    mappings = [
        _mapping(track, "T01", "runtime-t01", "ITEM-A"),
        _mapping(track, "T02", "runtime-t02", "ITEM-B"),
    ]
    tasks = {
        "runtime-t01": TaskDB(id="runtime-t01", status="completed"),
        "runtime-t02": TaskDB(id="runtime-t02", status="failed"),
    }
    repository = _PlanningRepositoryFake(
        revisions=[category, track],
        mappings=mappings,
        lineage=_lineage(track),
        proposals=[_proposal()],
    )
    projection = PlanningStatusProjectionService(
        uow_factory=lambda: _UnitOfWorkFake(repository, _SessionFake(tasks))
    ).project_goal(context=_context(), goal_id="goal-a")

    assert projection["organization_goal_status"] == "blocked"
    assert projection["category"]["payload"]["meta"]["total_items"] == 2
    category_items = {
        row["id"]: row for group in projection["category"]["payload"]["categories"] for row in group["items"]
    }
    assert category_items["ITEM-A"]["status"] == "completed"
    assert category_items["ITEM-B"]["status"] == "partial"
    projected_track = projection["tracks"][0]
    task_status = {row["id"]: row["status"] for row in projected_track["payload"]["tasks"]}
    assert task_status == {"T01": "done", "T02": "blocked", "T03": "todo"}
    assert projected_track["blockers"] == ["T02"]
    assert projected_track["drift"] is False
    assert projected_track["source_category_artifact_revision_id"] == category.id
    assert projected_track["source_category_revision"] == category.revision
    assert projected_track["source_category_digest"] == category.content_digest
    proposal = projection["proposals"][0]
    assert proposal["proposal_id"] == "proposal-a"
    assert proposal["proposal_revision"] == 1
    assert proposal["proposal_digest"] == "sha256:" + "2" * 64
    assert proposal["approval_request_id"] == "approval-a"


def test_replan_preserves_completed_lineage_replaces_declared_work_and_writes_no_tasks() -> None:
    category = _category()
    source = _track(category)
    mappings = [
        _mapping(source, "T01", "runtime-t01", "ITEM-A"),
        _mapping(source, "T02", "runtime-t02", "ITEM-B"),
        _mapping(source, "T03", "runtime-t03", "ITEM-B"),
    ]
    tasks = {
        "runtime-t01": TaskDB(id="runtime-t01", status="completed"),
        "runtime-t02": TaskDB(id="runtime-t02", status="todo"),
        "runtime-t03": TaskDB(id="runtime-t03", status="todo"),
    }
    repository = _PlanningRepositoryFake(
        revisions=[category, source],
        mappings=mappings,
        lineage=_lineage(source),
    )
    replacement = copy.deepcopy(source.payload)
    retained_t02 = copy.deepcopy(replacement["tasks"][1])
    new_t04 = copy.deepcopy(replacement["tasks"][2])
    new_t04.update(
        id="T04",
        title="Verify replacement",
        depends_on=["T02"],
        source_category_item_ids=["ITEM-B"],
    )
    replacement["tasks"] = [retained_t02, new_t04]
    replacement["milestones"][0]["task_ids"] = ["T01", "T02", "T04"]
    replacement["critical_path_tasks"] = ["T01", "T02", "T04"]
    service = PlanningReplanService(uow_factory=lambda: _UnitOfWorkFake(repository, _SessionFake(tasks)))

    result = service.create_track_revision(
        context=_context(),
        source_track_revision_id=source.id,
        expected_track_digest=source.content_digest,
        expected_policy_hash=source.policy_hash,
        replacement_payload=replacement,
        replaced_plan_task_ids=["T03"],
        idempotency_key="replan-a",
    )

    assert result["retained_plan_task_ids"] == ["T01", "T02"]
    assert result["replaced_plan_task_ids"] == ["T03"]
    assert result["task_created"] is False
    assert result["queue_write"] is False
    assert result["budget_reservation_created"] is False
    new_revision = repository.get_revision(result["track_artifact_revision_id"])
    assert new_revision is not None
    assert new_revision.supersedes_revision_id == source.id
    assert new_revision.parent_revision_id == category.id
    dispositions = {row["id"]: row["replan_disposition"] for row in new_revision.payload["tasks"]}
    assert dispositions == {
        "T01": "preserved_completed",
        "T02": "retained",
        "T04": "new",
    }
    assert {
        (row.source_category_item_id, row.plan_task_id) for row in repository.list_lineage_for_track(new_revision.id)
    } == {("ITEM-A", "T01"), ("ITEM-B", "T02"), ("ITEM-B", "T04")}

    replay = service.create_track_revision(
        context=_context(),
        source_track_revision_id=source.id,
        expected_track_digest=source.content_digest,
        expected_policy_hash=source.policy_hash,
        replacement_payload=replacement,
        replaced_plan_task_ids=["T03"],
        idempotency_key="replan-a",
    )
    assert replay["track_artifact_revision_id"] == new_revision.id
    assert replay["replayed"] is True


def test_replan_rejects_foreign_scope_stale_digest_and_changed_retained_task() -> None:
    category = _category()
    source = _track(category)
    repository = _PlanningRepositoryFake(
        revisions=[category, source],
        mappings=[],
        lineage=_lineage(source),
    )
    service = PlanningReplanService(uow_factory=lambda: _UnitOfWorkFake(repository, _SessionFake({})))

    with pytest.raises(PlanningTransitionError, match="planning_scope_forbidden"):
        service.create_track_revision(
            context=_context(organization_id="organization-other"),
            source_track_revision_id=source.id,
            expected_track_digest=source.content_digest,
            expected_policy_hash=source.policy_hash,
            replacement_payload=source.payload,
            replaced_plan_task_ids=[],
            idempotency_key="foreign",
        )
    with pytest.raises(PlanningTransitionError, match="planning_revision_digest_mismatch"):
        service.create_track_revision(
            context=_context(),
            source_track_revision_id=source.id,
            expected_track_digest="stale",
            expected_policy_hash=source.policy_hash,
            replacement_payload=source.payload,
            replaced_plan_task_ids=[],
            idempotency_key="stale",
        )
    changed = copy.deepcopy(source.payload)
    changed["tasks"][0]["title"] = "Silently reinterpreted"
    with pytest.raises(PlanningTransitionError, match="planning_replan_retained_task_changed"):
        service.create_track_revision(
            context=_context(),
            source_track_revision_id=source.id,
            expected_track_digest=source.content_digest,
            expected_policy_hash=source.policy_hash,
            replacement_payload=changed,
            replaced_plan_task_ids=[],
            idempotency_key="changed",
        )
    assert len(repository.revisions) == 2
