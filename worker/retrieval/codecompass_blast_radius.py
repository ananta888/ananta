"""CRG-005: blast-radius query as a CodeCompass tool.

Bounded expansion over the existing CodeCompassGraphStore (CRG-001
stipulates that the tool must NOT call foreign CRG APIs directly).

Risk score uses a documented, versioned formula with clamped
components. The output names ``risk_model_version`` and a
``score_breakdown`` so the consumer can audit the model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore


RISK_MODEL_VERSION = "blast_radius.v1"
RISK_MODEL_DESCRIPTION = (
    "risk_score = clamp("
    "  0.45 * normalized(affected_files) "
    "+ 0.30 * normalized(affected_symbols) "
    "+ 0.20 * normalized(affected_tests_inverse) "
    "+ 0.05 * heuristic_factor, "
    "0.0, 1.0)"
)


@dataclass(frozen=True)
class BlastRadiusResult:
    seed_nodes: tuple[str, ...]
    affected_files: tuple[str, ...]
    affected_symbols: tuple[str, ...]
    affected_tests: tuple[str, ...]
    evidence_paths: tuple[str, ...]
    risk_score: float
    risk_model_version: str
    score_breakdown: dict[str, float]
    warnings: tuple[str, ...] = ()
    max_depth: int = 0
    include_tests: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed_nodes": list(self.seed_nodes),
            "affected_files": list(self.affected_files),
            "affected_symbols": list(self.affected_symbols),
            "affected_tests": list(self.affected_tests),
            "evidence_paths": list(self.evidence_paths),
            "risk_score": self.risk_score,
            "risk_model_version": self.risk_model_version,
            "score_breakdown": dict(self.score_breakdown),
            "warnings": list(self.warnings),
            "max_depth": self.max_depth,
            "include_tests": self.include_tests,
        }


def _normalize(value: float, cap: float) -> float:
    if cap <= 0:
        return 0.0
    return min(1.0, value / cap)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def compute_blast_radius(
    *,
    graph_store: CodeCompassGraphStore,
    seed_nodes: tuple[str, ...],
    changed_files: tuple[str, ...] = (),
    max_depth: int = 3,
    include_tests: bool = True,
    node_cap: int = 500,
    test_cap: int = 200,
    file_cap: int = 200,
) -> BlastRadiusResult:
    """Bounded reverse-traversal of the symbolgraph from the seeds.

    Works without CRG being installed. Reads only the existing
    CodeCompassGraphStore payload. The risk model is documented and
    versioned (RISK_MODEL_VERSION).
    """
    warnings: list[str] = []
    if max_depth <= 0:
        warnings.append("max_depth_zero_no_expansion")
    payload = graph_store.load()
    nodes_by_id: dict[str, Any] = payload.get("node_index", {}).get("by_id", {}) or {}
    incoming_index: dict[str, list[str]] = payload.get("incoming_index", {}) or {}
    outgoing_index: dict[str, list[str]] = payload.get("outgoing_index", {}) or {}

    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(s, 0) for s in seed_nodes]
    affected_files: set[str] = set()
    affected_symbols: set[str] = set()
    affected_tests: set[str] = set()
    evidence_paths: set[str] = set()

    while queue and len(visited) < node_cap:
        nid, depth = queue.pop(0)
        if nid in visited or depth > max_depth:
            continue
        visited.add(nid)
        node = nodes_by_id.get(nid) or {}
        file_path = str(node.get("file") or "")
        kind = str(node.get("kind") or "")
        if file_path:
            affected_files.add(file_path)
            evidence_paths.add(file_path)
        if kind.startswith("symbol_"):
            affected_symbols.add(nid)
        if kind == "test" or (node.get("attrs") or {}).get("test_kind"):
            affected_tests.add(nid)

        # BFS reverse: who depends on me?  incoming_index is nested:
        # {node_id: {edge_type: [edges]}}. An incoming edge stores
        # source_id pointing AT nid.
        for incoming_entry in (incoming_index.get(nid) or {}).values():
            for edge in incoming_entry:
                src = str(edge.get("source_id") or "").strip()
                if src and src not in visited:
                    queue.append((src, depth + 1))
        if include_tests and depth + 1 <= max_depth:
            for outgoing_entry in (outgoing_index.get(nid) or {}).values():
                for edge in outgoing_entry:
                    tgt = str(edge.get("target_id") or "").strip()
                    if tgt and tgt not in visited:
                        queue.append((tgt, depth + 1))

    if len(visited) >= node_cap:
        warnings.append("node_cap_reached")

    affected_files_sorted = tuple(sorted(affected_files))[:file_cap]
    affected_symbols_sorted = tuple(sorted(affected_symbols))[:node_cap]
    affected_tests_sorted = tuple(sorted(affected_tests))[:test_cap]
    evidence_paths_sorted = tuple(sorted(evidence_paths))

    n_files = len(affected_files_sorted)
    n_symbols = len(affected_symbols_sorted)
    n_tests = len(affected_tests_sorted)
    file_norm = _normalize(n_files, cap=50)
    symbol_norm = _normalize(n_symbols, cap=100)
    # inverse: more tests covering the seed = LOWER risk
    test_norm = _normalize(n_tests, cap=20)
    test_inverse = 1.0 - test_norm
    heuristic_factor = 1.0 if (n_files and not n_tests) else 0.0

    breakdown = {
        "files": round(0.45 * file_norm, 4),
        "symbols": round(0.30 * symbol_norm, 4),
        "tests_inverse": round(0.20 * test_inverse, 4),
        "heuristic": round(0.05 * heuristic_factor, 4),
    }
    risk_score = _clamp(sum(breakdown.values()))

    if changed_files:
        # If the caller's changed_files overlap with affected_files, the
        # seed already explains the change: low additional risk.
        overlap = set(changed_files) & set(affected_files_sorted)
        if overlap:
            warnings.append("seed_explains_change")

    return BlastRadiusResult(
        seed_nodes=seed_nodes,
        affected_files=affected_files_sorted,
        affected_symbols=affected_symbols_sorted,
        affected_tests=affected_tests_sorted,
        evidence_paths=evidence_paths_sorted,
        risk_score=risk_score,
        risk_model_version=RISK_MODEL_VERSION,
        score_breakdown=breakdown,
        warnings=tuple(warnings),
        max_depth=max_depth,
        include_tests=include_tests,
    )


__all__ = [
    "RISK_MODEL_VERSION",
    "RISK_MODEL_DESCRIPTION",
    "BlastRadiusResult",
    "compute_blast_radius",
]