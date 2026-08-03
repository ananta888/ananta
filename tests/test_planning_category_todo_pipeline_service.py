from __future__ import annotations

import json
from typing import Any

import pytest

from agent.db_models import PlanningArtifactRevisionDB
from agent.services.planning_category_pipeline_service import (
    PlanningCategoryPipelineService,
)
from agent.services.planning_evidence_resolver_service import (
    AssignmentEvidenceContext,
    PlanningEvidenceError,
)


class _PlanningRepositoryFake:
    def __init__(self) -> None:
        self.revisions: list[PlanningArtifactRevisionDB] = []
        self.lock_keys: list[str] = []

    def acquire_scope_lock(self, scope_key: str) -> None:
        self.lock_keys.append(scope_key)

    def next_revision_number(self, *, artifact_id: str) -> int:
        return 1 + max(
            (row.revision for row in self.revisions if row.artifact_id == artifact_id),
            default=0,
        )

    def latest_revision(self, *, artifact_id: str) -> PlanningArtifactRevisionDB | None:
        matches = [row for row in self.revisions if row.artifact_id == artifact_id]
        return max(matches, key=lambda row: row.revision) if matches else None

    def add_revision(self, revision: PlanningArtifactRevisionDB) -> PlanningArtifactRevisionDB:
        self.revisions.append(revision)
        return revision


class _PlanningUnitOfWorkFake:
    def __init__(self, planning: _PlanningRepositoryFake) -> None:
        self.planning = planning
        self.session = None
        self.committed = False
        self.rolled_back = False

    def __enter__(self) -> _PlanningUnitOfWorkFake:
        return self

    def __exit__(self, exc_type, _exc_value, _traceback) -> None:
        self.committed = exc_type is None
        self.rolled_back = exc_type is not None


def _context(*, artifact_hashes: dict[str, str] | None = None) -> AssignmentEvidenceContext:
    return AssignmentEvidenceContext(
        task_id="research-task-a",
        assignment_id="research-assignment-a",
        dispatch_lease_id="research-lease-a",
        tenant_id="tenant-a",
        scope="organization:organization-a",
        source_catalog_id="catalog-a",
        source_catalog_hash="a" * 64,
        allowed_source_refs=frozenset(),
        allowed_run_refs=frozenset(),
        artifact_hashes=dict(artifact_hashes or {}),
    )


def _catalog() -> dict[str, Any]:
    return {
        "source_catalog_id": "catalog-a",
        "source_catalog_hash": "a" * 64,
        "sources": [],
    }


def _ungrounded_category() -> dict[str, Any]:
    return {
        "version": 1,
        "created": "test",
        "updated": "test",
        "project": "organization-a",
        "review_basis": {
            "reviewed_commit_range": "not-provided",
            "review_goal": "identify missing evidence",
        },
        "categories": [
            {
                "name": "research",
                "label": "Research",
                "items": [
                    {
                        "id": "ITEM-A",
                        "title": "Acquire assignment-bound evidence",
                        "status": "open",
                        "priority": "P1",
                        "risk": "high",
                        "type": "research",
                        "depends_on": [],
                        "acceptance_criteria": ["Bound evidence is supplied by the Hub."],
                        "evidence_claim_refs": ["CLM_0001"],
                    }
                ],
            }
        ],
        "meta": {
            "total_items": 999,
            "by_status": {"completed": 999, "partial": 0, "open": 0},
            "notes": ["Worker-provided summary is deliberately stale."],
            "recommended_order": [],
        },
        "planning_quality_profile": {
            "schema": "category_todo_quality_profile.v1",
            "source_catalog_id": "catalog-a",
            "source_catalog_hash": "a" * 64,
            "allowed_source_refs": [],
            "allowed_run_refs": [],
            "research_summary": "No Assignment-bound evidence was supplied.",
            "claims": [
                {
                    "claim_id": "CLM_0001",
                    "text": "The evidence gap remains unresolved.",
                    "claim_type": "uncertain",
                    "citation_refs": [],
                    "confidence": "unverified",
                }
            ],
            "unsupported_notes": ["Positive grounding remains release evidence."],
            "grounding_status": "unverified",
            "grounding_reason": "assignment_evidence_missing",
        },
    }


def _persist(
    service: PlanningCategoryPipelineService,
    *,
    context: AssignmentEvidenceContext | None = None,
    raw_output: str | None = None,
    assignment_id: str = "research-assignment-a",
    dispatch_lease_id: str = "research-lease-a",
    runtime_artifact_hashes: dict[str, str] | None = None,
    repair_fn=None,
) -> dict[str, Any]:
    evidence_context = context or _context()
    return service.persist_research_result(
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        goal_id="goal-a",
        task_id="research-task-a",
        assignment_id=assignment_id,
        dispatch_lease_id=dispatch_lease_id,
        worker_id="planner-worker-a",
        artifact_id="category-artifact-a",
        raw_output=raw_output if raw_output is not None else json.dumps(_ungrounded_category()),
        evidence_context=evidence_context,
        source_catalog=_catalog(),
        tool_run_catalog=[],
        prompt_hash="prompt-hash-a",
        policy_hash="policy-hash-a",
        runtime_artifact_hashes=runtime_artifact_hashes,
        repair_fn=repair_fn,
    )


@pytest.mark.parametrize(
    ("assignment_id", "dispatch_lease_id", "reason"),
    [
        ("assignment-other", "research-lease-a", "category_assignment_mismatch"),
        ("research-assignment-a", "lease-other", "category_dispatch_lease_mismatch"),
    ],
)
def test_pipeline_rejects_foreign_assignment_or_lease_before_persistence(
    assignment_id: str,
    dispatch_lease_id: str,
    reason: str,
) -> None:
    repository = _PlanningRepositoryFake()
    service = PlanningCategoryPipelineService(uow_factory=lambda: _PlanningUnitOfWorkFake(repository))

    with pytest.raises(ValueError, match=reason):
        _persist(
            service,
            assignment_id=assignment_id,
            dispatch_lease_id=dispatch_lease_id,
        )
    assert repository.revisions == []


def test_pipeline_rejects_changed_assignment_artifact_hash_before_persistence() -> None:
    repository = _PlanningRepositoryFake()
    service = PlanningCategoryPipelineService(uow_factory=lambda: _PlanningUnitOfWorkFake(repository))

    with pytest.raises(PlanningEvidenceError, match="evidence_artifact_hash_mismatch"):
        _persist(
            service,
            context=_context(artifact_hashes={"artifact:research-input": "expected"}),
            runtime_artifact_hashes={"artifact:research-input": "tampered"},
        )
    assert repository.revisions == []


def test_pipeline_recomputes_worker_summary_but_keeps_ungrounded_result_failed() -> None:
    repository = _PlanningRepositoryFake()
    service = PlanningCategoryPipelineService(uow_factory=lambda: _PlanningUnitOfWorkFake(repository))

    result = _persist(service)

    assert result["status"] == "failed"
    assert result["promotable"] is False
    assert result["materialized_task_ids"] == []
    assert len(repository.revisions) == 1
    revision = repository.revisions[0]
    assert revision.payload["meta"]["total_items"] == 1
    assert revision.payload["meta"]["by_status"]["open"] == 1
    assert revision.payload["meta"]["recommended_order"] == ["ITEM-A"]
    assert revision.validation_result["grounding"]["status"] == "unverified"
    assert revision.validation_result["grounding"]["reason"] == "category_claims_unverified"
    assert revision.execution_provenance["assignment_id"] == "research-assignment-a"
    assert revision.execution_provenance["dispatch_lease_id"] == "research-lease-a"
    assert revision.allowed_source_refs == []
    assert revision.allowed_run_refs == []


def test_pipeline_allows_at_most_one_bounded_repair_and_still_fails_closed() -> None:
    repository = _PlanningRepositoryFake()
    service = PlanningCategoryPipelineService(uow_factory=lambda: _PlanningUnitOfWorkFake(repository))
    prompts: list[str] = []

    def repair(prompt: str) -> str:
        prompts.append(prompt)
        return "{}"

    result = _persist(service, raw_output="not-json", repair_fn=repair)

    assert result["status"] == "failed"
    assert result["repair_attempt_count"] == 1
    assert len(prompts) == 1
    assert "Never invent SRC_* or RUN_* identifiers." in prompts[0]
    assert len(repository.revisions) == 1
    assert repository.revisions[0].validation_result["repair_attempt_count"] == 1
    assert result["materialized_task_ids"] == []


def test_pipeline_requires_complete_hub_scope_before_opening_unit_of_work() -> None:
    repository = _PlanningRepositoryFake()
    service = PlanningCategoryPipelineService(uow_factory=lambda: _PlanningUnitOfWorkFake(repository))

    with pytest.raises(ValueError, match="organization_id_required"):
        service.persist_research_result(
            tenant_id="tenant-a",
            project_id="project-a",
            organization_id="",
            goal_id="goal-a",
            task_id="research-task-a",
            assignment_id="research-assignment-a",
            dispatch_lease_id="research-lease-a",
            worker_id="planner-worker-a",
            artifact_id="category-artifact-a",
            raw_output="{}",
            evidence_context=_context(),
            source_catalog=_catalog(),
            tool_run_catalog=[],
            prompt_hash="prompt-hash-a",
            policy_hash="policy-hash-a",
        )
    assert repository.revisions == []
