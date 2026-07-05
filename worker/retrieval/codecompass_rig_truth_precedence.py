"""RIG-009: truth-precedence between RIG and RAG.

When a coverage_status=complete RIG exists for a build/test question,
semantic (RAG) matches without RIG evidence are marked as
``weak_support``. When coverage_status is partial|unknown, missing
RIG evidence becomes ``unknown_coverage`` instead of negative evidence.

Per CCRIG-DD-008 / DD-016 / CCRIG-DD-002: deterministic repository
truth wins over heuristic semantic context, but only inside
``coverage_status=complete``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.services.tools.graph_evidence import POLICY_ALLOWED_TRUST
from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore


TRUTH_PRECEDENCE_VERSION = "truth_precedence.v1"


@dataclass(frozen=True)
class PrecedenceDecision:
    topic: str
    rig_status: str  # complete | partial | unknown | unavailable
    rig_evidence_present: bool
    rag_present: bool
    final_support: str  # strong | weak_support | unknown_coverage | contradiction
    rationale: str
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "rig_status": self.rig_status,
            "rig_evidence_present": self.rig_evidence_present,
            "rag_present": self.rag_present,
            "final_support": self.final_support,
            "rationale": self.rationale,
            "warnings": list(self.warnings),
        }


def _coverage_status(graph_store: CodeCompassGraphStore) -> str:
    diag = graph_store.load().get("diagnostics") or {}
    ri = diag.get("repository_intelligence") or {}
    if ri.get("status") != "ready":
        return "unavailable"
    return ri.get("coverage_status") or "unknown"


def _rig_has_topic_evidence(graph_store: CodeCompassGraphStore, topic: str) -> bool:
    """Return True if the RIG has any record referencing ``topic``."""
    payload = graph_store.load()
    needles = topic.lower()
    for n in payload.get("rig_nodes") or []:
        attrs = (n.get("attrs") or {})
        if isinstance(attrs, dict):
            for key in ("name", "id"):
                value = str(attrs.get(key) or "").lower()
                if needles in value:
                    return True
        # check source_files lists
        files = attrs.get("source_files") or []
        if any(needles in str(f).lower() for f in files):
            return True
    for edge in payload.get("rig_edges") or []:
        for k in ("from_id", "to_id", "kind"):
            if needles in str(edge.get(k) or "").lower():
                return True
    return False


def decide_truth_precedence(
    *,
    graph_store: CodeCompassGraphStore,
    topic: str,
    rag_present: bool,
) -> PrecedenceDecision:
    """Apply truth-precedence rules for a single topic / question."""
    rig_status = _coverage_status(graph_store)
    rig_present = rig_status != "unavailable" and _rig_has_topic_evidence(
        graph_store, topic)
    warnings: list[str] = []

    if rig_status == "unavailable":
        # No RIG: RAG stands on its own.
        if rag_present:
            return PrecedenceDecision(
                topic=topic, rig_status=rig_status,
                rig_evidence_present=False, rag_present=True,
                final_support="weak_support",
                rationale="no RIG available; RAG is the only signal",
            )
        return PrecedenceDecision(
            topic=topic, rig_status=rig_status,
            rig_evidence_present=False, rag_present=False,
            final_support="unknown_coverage",
            rationale="no RIG, no RAG; nothing to report",
        )

    if rig_status == "complete" and rig_present:
        if rag_present:
            warnings.append("rag_present_but_rig_authoritative")
        return PrecedenceDecision(
            topic=topic, rig_status=rig_status,
            rig_evidence_present=True, rag_present=rag_present,
            final_support="strong",
            rationale="RIG coverage=complete and topic is represented",
            warnings=tuple(warnings),
        )

    if rig_status == "complete" and not rig_present:
        # complete RIG but topic missing — this *is* negative evidence
        # (CCRIG-DD-008: only complete is authoritative).
        return PrecedenceDecision(
            topic=topic, rig_status=rig_status,
            rig_evidence_present=False, rag_present=rag_present,
            final_support="contradiction" if rag_present else "unknown_coverage",
            rationale=("RIG coverage=complete but topic absent: "
                       "negative evidence" if not rag_present else
                       "RAG contradicts complete RIG: warn"),
        )

    # partial | unknown: missing evidence is *not* negative.
    return PrecedenceDecision(
        topic=topic, rig_status=rig_status,
        rig_evidence_present=rig_present, rag_present=rag_present,
        final_support="unknown_coverage" if not rig_present else "weak_support",
        rationale=(f"RIG coverage={rig_status}: missing topic is "
                   "unknown_coverage, not negative evidence"),
    )


__all__ = [
    "TRUTH_PRECEDENCE_VERSION",
    "PrecedenceDecision",
    "decide_truth_precedence",
    "POLICY_ALLOWED_TRUST",  # re-export for tests
]