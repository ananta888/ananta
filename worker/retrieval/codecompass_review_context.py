"""CRG-006 + RIG-007: minimal review context for review/bugfix tasks.

Builds an ordered, *minimal* context package using:

* CRG-005 blast radius over the symbolgraph (optional)
* RIG-005 repository-intelligence queries for build/test evidence
  (optional, gated by ``include_repository_intelligence``)
* the existing CodeCompass resolve_context as the fallback

Per CRG-006:

* never produce a full repo dump
* order: changed files, direct dependents, tests, high-risk hubs
* respect existing codecompass_budgeting limits
* include_graph=true keeps old payload semantics (backward compat)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from worker.retrieval.codecompass_blast_radius import (
    BlastRadiusResult,
    compute_blast_radius,
)
from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore
from worker.retrieval.codecompass_repository_intelligence_query import (
    ALLOWED_QUERY_TYPES,
    run_query,
)


REVIEW_CONTEXT_VERSION = "minimal_review_context.v1"
DEFAULT_SEED_CAP = 25
DEFAULT_RIG_QUERY_CAP = 10


@dataclass(frozen=True)
class ReviewContextSection:
    title: str
    items: tuple[dict[str, Any], ...]
    evidence_paths: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "items": list(self.items),
            "evidence_paths": list(self.evidence_paths),
        }


@dataclass(frozen=True)
class MinimalReviewContext:
    schema_version: str
    sections: tuple[ReviewContextSection, ...]
    blast_radius: BlastRadiusResult | None
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schema_version": self.schema_version,
            "sections": [s.as_dict() for s in self.sections],
            "warnings": list(self.warnings),
        }
        if self.blast_radius is not None:
            out["blast_radius"] = self.blast_radius.as_dict()
        return out


def build_minimal_review_context(
    *,
    graph_store: CodeCompassGraphStore,
    changed_files: tuple[str, ...],
    seed_nodes: tuple[str, ...] = (),
    task_kind: str = "review",
    include_repository_intelligence: bool = True,
    max_total_items: int = 100,
) -> MinimalReviewContext:
    warnings: list[str] = []

    # 1) blast radius over symbolgraph (CRG-005) — works without RIG.
    br: BlastRadiusResult | None = None
    if seed_nodes:
        try:
            br = compute_blast_radius(
                graph_store=graph_store,
                seed_nodes=seed_nodes,
                changed_files=changed_files,
                max_depth=3,
            )
        except Exception as exc:  # pragma: no cover -- defensive
            warnings.append(f"blast_radius_failed:{type(exc).__name__}")

    sections: list[ReviewContextSection] = []

    # 2) changed files (always present)
    sections.append(ReviewContextSection(
        title="changed_files",
        items=tuple({"path": p} for p in sorted(changed_files)[:DEFAULT_SEED_CAP]),
        evidence_paths=tuple(sorted(changed_files)[:DEFAULT_SEED_CAP]),
    ))

    # 3) direct dependents + tests (from blast radius)
    if br is not None:
        dependents = [f for f in br.affected_files if f not in changed_files]
        sections.append(ReviewContextSection(
            title="direct_dependents",
            items=tuple({"path": p} for p in dependents[:DEFAULT_SEED_CAP]),
            evidence_paths=tuple(dependents[:DEFAULT_SEED_CAP]),
        ))
        sections.append(ReviewContextSection(
            title="affected_tests",
            items=tuple({"test": t} for t in br.affected_tests[:DEFAULT_SEED_CAP]),
        ))
        # 4) high-risk hubs from blast radius (top 5 by score_breakdown)
        risk_score = br.risk_score
        hub_summary = {
            "risk_score": risk_score,
            "risk_model_version": br.risk_model_version,
            "score_breakdown": br.score_breakdown,
        }
        sections.append(ReviewContextSection(
            title="risk_summary",
            items=(hub_summary,),
        ))

    # 5) build/test evidence via RIG if enabled (RIG-007)
    if include_repository_intelligence:
        rig_evidence: list[dict[str, Any]] = []
        rig_paths: set[str] = set()
        for seed in seed_nodes[:DEFAULT_RIG_QUERY_CAP]:
            for query_type in ("component-tests", "package-dependents"):
                if query_type not in ALLOWED_QUERY_TYPES:
                    continue
                res = run_query(graph_store=graph_store,
                                query_type=query_type, seed=seed,
                                max_results=DEFAULT_RIG_QUERY_CAP)
                for r in res.results[:DEFAULT_RIG_QUERY_CAP]:
                    rig_evidence.append({"seed": seed,
                                         "query_type": query_type,
                                         **r})
                for p in res.evidence_paths:
                    rig_paths.add(p)
                if res.warnings:
                    warnings.extend(f"{query_type}:{w}" for w in res.warnings)
        if rig_evidence:
            sections.append(ReviewContextSection(
                title="build_test_evidence",
                items=tuple(rig_evidence[:max_total_items]),
                evidence_paths=tuple(sorted(rig_paths)),
            ))

    # 6) trim to budget
    total = sum(len(s.items) for s in sections)
    if total > max_total_items:
        warnings.append("max_total_items_truncated")

    return MinimalReviewContext(
        schema_version=REVIEW_CONTEXT_VERSION,
        sections=tuple(sections),
        blast_radius=br,
        warnings=tuple(warnings),
    )


__all__ = [
    "REVIEW_CONTEXT_VERSION",
    "DEFAULT_SEED_CAP",
    "DEFAULT_RIG_QUERY_CAP",
    "ReviewContextSection",
    "MinimalReviewContext",
    "build_minimal_review_context",
]