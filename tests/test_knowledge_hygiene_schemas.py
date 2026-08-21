from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from agent.services.knowledge_hygiene.analysis import analyze_claims
from ananta_contracts.knowledge_hygiene import (
    CoverageState,
    CuratedWikiPage,
    DecisionKind,
    KnowledgeClaim,
    KnowledgeConflictDecision,
    KnowledgeHealthSnapshot,
    KnowledgeHygieneRun,
    RunState,
    SourceRevisionBinding,
    build_correction_proposal,
    canonical_digest,
)


ROOT = Path(__file__).resolve().parents[1]


def _schema(name: str) -> dict:
    return json.loads((ROOT / "schemas/knowledge_hygiene" / name).read_text(encoding="utf-8"))


def test_versioned_wire_contracts_validate_strict_instances() -> None:
    left = KnowledgeClaim(
        claim_id="KCL_" + "a" * 24,
        project_id="project-a",
        revision=1,
        subject="service",
        predicate="replicas",
        value=2,
        source_id="SRC_0001",
        source_revision="rev-1",
        source_locator="left.md#replicas",
        source_content_sha256="a" * 64,
        extraction_run_id="RUN_0001",
        unit="count",
        created_at=1.0,
    )
    right = KnowledgeClaim(
        claim_id="KCL_" + "b" * 24,
        project_id="project-a",
        revision=1,
        subject="service",
        predicate="replicas",
        value=3,
        source_id="SRC_0002",
        source_revision="rev-1",
        source_locator="right.md#replicas",
        source_content_sha256="b" * 64,
        extraction_run_id="RUN_0001",
        unit="count",
        created_at=1.0,
    )
    conflict = analyze_claims((left, right), max_candidate_pairs=10, now=2.0).conflicts[0]
    decision = KnowledgeConflictDecision(
        decision_id="decision-1",
        conflict_id=conflict.conflict_id,
        project_id="project-a",
        expected_conflict_version=1,
        actor_id="human-1",
        actor_kind="human",
        decision=DecisionKind.KEEP_BOTH,
        rationale="Both values apply under explicit qualifiers.",
        qualifiers=("left=legacy", "right=current"),
        basis_digest=conflict.basis_digest,
        created_at=3.0,
    )
    page = CuratedWikiPage(
        page_id="KWP_" + "c" * 24,
        project_id="project-a",
        slug="service-capacity",
        title="Service capacity",
        revision=1,
        body_markdown="Grounded page.",
        claim_refs=((left.claim_id, 1), (right.claim_id, 1)),
        conflict_refs=(conflict.conflict_id,),
        source_refs=("SRC_0001", "SRC_0002"),
        aliases=(),
        coverage=CoverageState.COMPLETE,
        created_at=3.0,
    )
    binding = SourceRevisionBinding(
        source_id="SRC_0001",
        source_revision="rev-1",
        content_sha256="a" * 64,
        allowed_locators=("left.md#replicas",),
    )
    assignment_digest = KnowledgeHygieneRun.calculate_assignment_digest(
        run_id="RUN_0001",
        project_id="project-a",
        source_bindings=(binding,),
        policy_version="knowledge_claim_precedence.v1",
        profile_name="deterministic-v1",
        budgets={"claims": 10},
    )
    run = KnowledgeHygieneRun(
        run_id="RUN_0001",
        project_id="project-a",
        state=RunState.PENDING,
        source_bindings=(binding,),
        policy_version="knowledge_claim_precedence.v1",
        profile_name="deterministic-v1",
        budgets={"claims": 10},
        actor_id="human-1",
        coverage=CoverageState.UNKNOWN,
        assignment_digest=assignment_digest,
        created_at=1.0,
        updated_at=1.0,
    )
    health_basis = canonical_digest({"project": "project-a", "revision": 1})
    health = KnowledgeHealthSnapshot(
        snapshot_id="KHS_" + "d" * 24,
        project_id="project-a",
        as_of=3.0,
        scope_version="project.v1",
        coverage=CoverageState.COMPLETE,
        counts={"claims": 2, "conflicts": 1, "open_conflicts": 1, "wiki_pages": 1},
        oldest_open_age_seconds=1.0,
        trend={"open_conflicts_delta": None},
        basis_digest=health_basis,
    )
    correction = build_correction_proposal(
        correction_id="correction-1",
        project_id="project-a",
        conflict_id=conflict.conflict_id,
        source_id="SRC_0001",
        source_revision="rev-1",
        source_locator="left.md#replicas",
        base_content_sha256="a" * 64,
        proposed_content="replicas: 3\n",
        proposed_by_run_id="RUN_0001",
        created_at=4.0,
    )
    samples = {
        "knowledge_claim.v1.json": left.to_dict(),
        "knowledge_conflict.v1.json": conflict.to_dict(),
        "knowledge_conflict_decision.v1.json": decision.to_dict(),
        "curated_wiki_page.v1.json": page.to_dict(),
        "knowledge_hygiene_run.v1.json": run.to_dict(),
        "knowledge_health_snapshot.v1.json": health.to_dict(),
        "knowledge_correction.v1.json": correction.to_dict(),
    }

    for name, sample in samples.items():
        Draft202012Validator(_schema(name)).validate(sample)
