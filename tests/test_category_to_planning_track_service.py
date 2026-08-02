from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agent.db_models import PlanningArtifactRevisionDB, PlanningLineageDB
from agent.services.category_to_planning_track_service import (
    CategoryToPlanningTrackError,
    CategoryToPlanningTrackService,
)
from agent.services.planning_category_contract_service import stable_planning_digest

FIXTURE = Path(__file__).resolve().parent / "fixtures/planning_tracks/small_track.json"


class _PlanningRepositoryFake:
    def __init__(self, category: PlanningArtifactRevisionDB) -> None:
        self.revisions = [category]
        self.lineage: list[PlanningLineageDB] = []

    def acquire_scope_lock(self, _scope_key: str) -> None:
        return None

    def get_revision(self, revision_id: str, *, for_update: bool = False):
        del for_update
        return next((row for row in self.revisions if row.id == revision_id), None)

    def next_revision_number(self, *, artifact_id: str) -> int:
        return 1 + max(
            (row.revision for row in self.revisions if row.artifact_id == artifact_id),
            default=0,
        )

    def latest_revision(self, *, artifact_id: str):
        rows = [row for row in self.revisions if row.artifact_id == artifact_id]
        return max(rows, key=lambda row: row.revision) if rows else None

    def add_revision(self, revision: PlanningArtifactRevisionDB):
        self.revisions.append(revision)
        return revision

    def add_lineage(self, rows) -> None:
        self.lineage.extend(rows)


class _UnitOfWorkFake:
    def __init__(self, repository: _PlanningRepositoryFake) -> None:
        self.planning = repository
        self.session = None

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        return None


def _category(*, status: str = "promoted") -> PlanningArtifactRevisionDB:
    payload = {
        "categories": [
            {
                "name": "delivery",
                "label": "Delivery",
                "items": [
                    {"id": "ITEM-A", "title": "Analyze", "depends_on": []},
                    {"id": "ITEM-B", "title": "Implement", "depends_on": ["ITEM-A"]},
                ],
            }
        ]
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
        status=status,
        payload=payload,
        content_digest=stable_planning_digest(payload),
        schema_ref="todos/todo.schema.json",
        schema_hash="schema-category",
        policy_hash="policy-a",
        allowed_source_refs=[],
        allowed_run_refs=[],
    )


def _track_payload(*, source_item: str, task_ids: list[str]) -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["track"] = f"track-{source_item.lower()}"
    payload["source_category_item_ids"] = [source_item]
    payload["tasks"] = [row for row in payload["tasks"] if row["id"] in task_ids]
    payload["milestones"][0]["task_ids"] = list(task_ids)
    payload["critical_path_tasks"] = list(task_ids)
    for task in payload["tasks"]:
        task["source_category_item_ids"] = [source_item]
        task["depends_on"] = []
    payload["tasks_status_summary"]["total"] = 999
    return payload


def _candidates() -> list[dict]:
    first = _track_payload(source_item="ITEM-A", task_ids=["T01"])
    second = _track_payload(source_item="ITEM-B", task_ids=["T02", "T03"])
    second["tasks"][0]["depends_on"] = ["track-a:T01"]
    second["tasks"][1]["depends_on"] = ["T02"]
    return [
        {"artifact_id": "track-a", "payload": first},
        {"artifact_id": "track-b", "payload": second},
    ]


def _service(repository: _PlanningRepositoryFake) -> CategoryToPlanningTrackService:
    return CategoryToPlanningTrackService(uow_factory=lambda: _UnitOfWorkFake(repository))


def _derive(service: CategoryToPlanningTrackService, category: PlanningArtifactRevisionDB, **overrides):
    arguments = {
        "category_revision_id": category.id,
        "expected_category_digest": category.content_digest,
        "expected_policy_hash": category.policy_hash,
        "track_candidates": _candidates(),
        "exclusions": {},
        "worker_id": "planner-worker-a",
        "assignment_id": "assignment-a",
        "dispatch_lease_id": "lease-a",
        "prompt_hash": "prompt-a",
    }
    arguments.update(overrides)
    return service.derive_tracks(**arguments)


def test_promoted_category_derives_lossless_tracks_and_recomputes_summaries() -> None:
    category = _category()
    repository = _PlanningRepositoryFake(category)

    result = _derive(_service(repository), category)

    assert len(result["track_revisions"]) == 2
    assert result["materialized_task_ids"] == []
    tracks = [row for row in repository.revisions if row.artifact_type == "planning_track"]
    assert {row.parent_revision_id for row in tracks} == {category.id}
    assert {row.execution_provenance["source_category_digest"] for row in tracks} == {category.content_digest}
    assert {row.execution_provenance["assignment_id"] for row in tracks} == {"assignment-a"}
    assert {row.execution_provenance["dispatch_lease_id"] for row in tracks} == {"lease-a"}
    assert {row.payload["tasks_status_summary"]["total"] for row in tracks} == {1, 2}
    assert {(row.source_category_item_id, row.plan_task_id) for row in repository.lineage} == {
        ("ITEM-A", "T01"),
        ("ITEM-B", "T02"),
        ("ITEM-B", "T03"),
    }


@pytest.mark.parametrize(
    ("expected_digest", "expected_policy", "status", "reason"),
    [
        ("stale", "policy-a", "promoted", "category_digest_mismatch"),
        (None, "policy-stale", "promoted", "category_policy_hash_stale"),
        (None, "policy-a", "valid", "category_revision_not_promoted"),
    ],
)
def test_category_preconditions_fail_before_track_or_lineage_writes(
    expected_digest: str | None,
    expected_policy: str,
    status: str,
    reason: str,
) -> None:
    category = _category(status=status)
    repository = _PlanningRepositoryFake(category)

    with pytest.raises(CategoryToPlanningTrackError, match=reason):
        _derive(
            _service(repository),
            category,
            expected_category_digest=expected_digest or category.content_digest,
            expected_policy_hash=expected_policy,
        )
    assert repository.revisions == [category]
    assert repository.lineage == []


@pytest.mark.parametrize(
    ("mutate", "detail"),
    [
        (
            lambda rows: rows[1]["payload"].update(source_category_item_ids=["ITEM-A", "ITEM-B"]),
            "category_item_mapped_more_than_once:ITEM-A",
        ),
        (
            lambda rows: rows[1]["payload"]["tasks"][0].update(depends_on=[]),
            "category_dependency_not_translated:ITEM-A->ITEM-B",
        ),
        (
            lambda rows: rows[1]["payload"]["tasks"][0].update(depends_on=["T01", "UNKNOWN-TASK"]),
            "planning_task_dependency_unknown:T02->UNKNOWN-TASK",
        ),
    ],
)
def test_overlap_missing_translation_and_unknown_dependencies_fail_atomically(mutate, detail: str) -> None:
    category = _category()
    repository = _PlanningRepositoryFake(category)
    candidates = _candidates()
    mutate(candidates)

    with pytest.raises(CategoryToPlanningTrackError) as error:
        _derive(_service(repository), category, track_candidates=candidates)
    assert detail in error.value.details
    assert repository.revisions == [category]
    assert repository.lineage == []


def test_uncovered_item_requires_an_explicit_exclusion_reason() -> None:
    category = _category()
    repository = _PlanningRepositoryFake(category)
    only_first = [_candidates()[0]]

    with pytest.raises(CategoryToPlanningTrackError) as error:
        _derive(_service(repository), category, track_candidates=only_first)
    assert "category_item_uncovered:ITEM-B" in error.value.details

    result = _derive(
        _service(repository),
        category,
        track_candidates=only_first,
        exclusions={"ITEM-B": "Explicitly deferred by the Hub."},
    )
    assert result["excluded_category_items"] == {"ITEM-B": "Explicitly deferred by the Hub."}
    assert len(result["track_revisions"]) == 1


def test_cross_track_cycle_and_inverted_category_dependency_are_rejected() -> None:
    category = _category()
    repository = _PlanningRepositoryFake(category)
    candidates = copy.deepcopy(_candidates())
    candidates[0]["payload"]["tasks"][0]["depends_on"] = ["T02"]

    with pytest.raises(CategoryToPlanningTrackError) as error:
        _derive(_service(repository), category, track_candidates=candidates)
    assert "category_dependency_inverted:ITEM-A->ITEM-B" in error.value.details
    assert "planning_cross_track_dependency_cycle" in error.value.details
    assert repository.revisions == [category]
    assert repository.lineage == []
