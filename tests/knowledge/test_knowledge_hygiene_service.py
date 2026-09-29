from __future__ import annotations

from dataclasses import replace

import pytest

from agent.repositories.knowledge_hygiene_repository import InMemoryKnowledgeHygieneRepository
from agent.services.knowledge_hygiene.config import KnowledgeHygieneConfig
from agent.services.knowledge_hygiene.service import (
    KnowledgeHygieneService,
    KnowledgeHygieneServiceError,
)
from ananta_contracts.knowledge_hygiene import (
    ConflictState,
    CoverageState,
    SourceRevisionBinding,
    WorkerKnowledgeHygieneResult,
)


class Clock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        self.value += 1.0
        return self.value


def _binding(source_id: str, revision: str, digest: str, locator: str) -> SourceRevisionBinding:
    return SourceRevisionBinding(
        source_id=source_id,
        source_revision=revision,
        content_sha256=digest,
        allowed_locators=(locator,),
    )


def _result(run, *, left_value: int, right_value: int, left_revision: str, right_revision: str) -> WorkerKnowledgeHygieneResult:
    claims = (
        {
            "project_id": "project-a",
            "subject": "service",
            "predicate": "replicas",
            "value": left_value,
            "unit": "count",
            "source_id": "SRC_0001",
            "source_revision": left_revision,
            "source_locator": "vault/left.md#replicas",
            "source_content_sha256": "a" * 64,
        },
        {
            "project_id": "project-a",
            "subject": "service",
            "predicate": "replicas",
            "value": right_value,
            "unit": "count",
            "source_id": "SRC_0002",
            "source_revision": right_revision,
            "source_locator": "vault/right.md#replicas",
            "source_content_sha256": "b" * 64,
        },
    )
    return WorkerKnowledgeHygieneResult(
        run_id=run.run_id,
        assignment_digest=run.assignment_digest,
        claims=claims,
        coverage=CoverageState.COMPLETE,
        processed_records=2,
    )


def _service(tmp_path, *, mode: str = "manual") -> KnowledgeHygieneService:
    return KnowledgeHygieneService(
        InMemoryKnowledgeHygieneRepository(),
        KnowledgeHygieneConfig(
            enabled=True,
            mode=mode,
            projection_dir=tmp_path / "wiki",
        ),
        clock=Clock(),
    )


def _run(service: KnowledgeHygieneService, run_id: str, left_revision: str, right_revision: str):
    run = service.start_run(
        run_id=run_id,
        project_id="project-a",
        source_bindings=(
            _binding("SRC_0001", left_revision, "a" * 64, "vault/left.md#replicas"),
            _binding("SRC_0002", right_revision, "b" * 64, "vault/right.md#replicas"),
        ),
        actor_id="human-1",
    )
    return service.runs.dispatch(
        project_id="project-a",
        run_id=run.run_id,
        worker_id="worker-1",
        lease_seconds=60,
    )


def test_hub_admission_analysis_human_decision_and_complete_recheck(tmp_path) -> None:
    service = _service(tmp_path)
    first_run = _run(service, "RUN_0001", "left-v1", "right-v1")
    _, first_claims = service.admit_worker_result(
        project_id="project-a",
        worker_id="worker-1",
        result=_result(
            first_run,
            left_value=2,
            right_value=3,
            left_revision="left-v1",
            right_revision="right-v1",
        ),
    )
    analysis = service.analyze_project("project-a", actor_id="human-1")
    conflict = analysis.conflicts[0]

    decided = service.decide_conflict(
        project_id="project-a",
        conflict_id=conflict.conflict_id,
        decision_id="decision-1",
        expected_version=conflict.version,
        basis_digest=conflict.basis_digest,
        actor_id="human-1",
        actor_kind="human",
        decision="keep_left",
        rationale="The left source is the approved operational record.",
    )

    assert decided.state is ConflictState.PENDING_REINGEST
    replayed = service.decide_conflict(
        project_id="project-a",
        conflict_id=conflict.conflict_id,
        decision_id="decision-1",
        expected_version=conflict.version,
        basis_digest=conflict.basis_digest,
        actor_id="human-1",
        actor_kind="human",
        decision="keep_left",
        rationale="The left source is the approved operational record.",
    )
    assert replayed == decided
    second_run = _run(service, "RUN_0002", "left-v2", "right-v2")
    _, second_claims = service.admit_worker_result(
        project_id="project-a",
        worker_id="worker-1",
        result=_result(
            second_run,
            left_value=2,
            right_value=2,
            left_revision="left-v2",
            right_revision="right-v2",
        ),
    )
    resolved = service.recheck_conflict(
        project_id="project-a",
        conflict_id=conflict.conflict_id,
        run_id="RUN_0002",
        left_claim_id=second_claims[0].claim_id,
        right_claim_id=second_claims[1].claim_id,
        actor_id="human-1",
    )

    assert len(first_claims) == 2
    assert resolved.state is ConflictState.RESOLVED
    assert resolved.resolved_by_run_id == "RUN_0002"
    assert len(service.repository.list_audit("project-a", conflict.conflict_id)) == 2


def test_result_replay_is_idempotent_but_divergent_replay_fails(tmp_path) -> None:
    service = _service(tmp_path)
    run = _run(service, "RUN_0001", "left-v1", "right-v1")
    result = _result(run, left_value=2, right_value=3, left_revision="left-v1", right_revision="right-v1")

    first_run, first_claims = service.admit_worker_result(project_id="project-a", worker_id="worker-1", result=result)
    replay_run, replay_claims = service.admit_worker_result(project_id="project-a", worker_id="worker-1", result=result)

    assert replay_run.result_digest == first_run.result_digest
    assert {item.record_digest for item in replay_claims} == {item.record_digest for item in first_claims}
    divergent = replace(result, rejected_records=1)
    with pytest.raises(KnowledgeHygieneServiceError, match="result_replay_mismatch"):
        service.admit_worker_result(project_id="project-a", worker_id="worker-1", result=divergent)


def test_cross_project_or_unbound_source_claim_is_rejected(tmp_path) -> None:
    service = _service(tmp_path)
    run = _run(service, "RUN_0001", "left-v1", "right-v1")
    valid = _result(run, left_value=2, right_value=3, left_revision="left-v1", right_revision="right-v1")
    tampered = replace(valid, claims=({**valid.claims[0], "project_id": "project-b"},))

    with pytest.raises(KnowledgeHygieneServiceError, match="cross_project_claim_rejected"):
        service.admit_worker_result(project_id="project-a", worker_id="worker-1", result=tampered)


def test_wiki_projection_requires_open_conflict_warning(tmp_path) -> None:
    service = _service(tmp_path)
    run = _run(service, "RUN_0001", "left-v1", "right-v1")
    _, claims = service.admit_worker_result(
        project_id="project-a",
        worker_id="worker-1",
        result=_result(run, left_value=2, right_value=3, left_revision="left-v1", right_revision="right-v1"),
    )
    conflict = service.analyze_project("project-a", actor_id="human-1").conflicts[0]
    proposal = {
        "slug": "service-capacity",
        "title": "Service capacity",
        "body_markdown": "Observed capacity with explicit evidence.",
        "claim_refs": [[item.claim_id, item.revision] for item in claims],
        "conflict_refs": [],
    }

    with pytest.raises(KnowledgeHygieneServiceError, match="wiki_conflict_warning_removed"):
        service.publish_page(project_id="project-a", proposal=proposal, actor_id="human-1")

    page = service.publish_page(
        project_id="project-a",
        proposal={**proposal, "conflict_refs": [conflict.conflict_id]},
        actor_id="human-1",
    )
    projected = tmp_path / "wiki" / "project-a" / "service-capacity.md"
    assert page.conflict_refs == (conflict.conflict_id,)
    assert projected.exists()
    assert conflict.conflict_id in projected.read_text(encoding="utf-8")


def test_unknown_health_uses_null_canonical_counts_and_observed_lower_bounds(tmp_path) -> None:
    service = _service(tmp_path, mode="observe")

    health = service.refresh_health("project-a", coverage=CoverageState.UNKNOWN)

    assert health.counts["claims"] is None
    assert health.counts["claims_observed"] == 0
    assert health.coverage is CoverageState.UNKNOWN


def test_observe_mode_cannot_record_human_decision(tmp_path) -> None:
    service = _service(tmp_path, mode="observe")
    with pytest.raises(KnowledgeHygieneServiceError, match="manual_mode_required"):
        service.decide_conflict(
            project_id="project-a",
            conflict_id="missing",
            decision_id="decision-1",
            expected_version=1,
            basis_digest="a" * 64,
            actor_id="human-1",
            actor_kind="human",
            decision="keep_both",
            rationale="Not reachable.",
        )
