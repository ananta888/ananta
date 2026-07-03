from __future__ import annotations
import time, uuid, json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class RetrievalMode(str, Enum):
    CODECOMPASS_ONLY = "codecompass_only"
    AUGMENT_ONLY = "augment_only"
    HYBRID = "hybrid"

class DiscardReason(str, Enum):
    DENIED_PATH = "denied_path"
    LOW_SCORE = "low_score"
    DUPLICATE = "duplicate"
    OVER_BUDGET = "over_budget"
    PROVIDER_DISABLED = "provider_disabled"

@dataclass
class GoldSet:
    gold_id: str
    query: str
    expected_files: list[str]
    expected_symbols: list[str] = field(default_factory=list)
    version: str = "1.0"
    notes: str = ""

@dataclass
class RetrievedResult:
    path: str
    score: float
    provider: str
    line_start: int | None = None
    line_end: int | None = None
    is_external: bool = False
    reason: str = ""

@dataclass
class DiscardedResult:
    path: str
    score: float
    provider: str
    reason: DiscardReason
    detail: str = ""

@dataclass
class RetrievalAuditReport:
    report_id: str
    query: str
    provider: str
    mode: str
    scope_paths: list[str]
    retrieved: list[RetrievedResult]
    discarded: list[DiscardedResult]
    routing_decision: str
    redactions_applied: int
    latency_ms: int
    created_at: float
    gold_comparison: dict[str, Any] | None = None

    def files_hit(self, gold: GoldSet) -> list[str]:
        retrieved_paths = {r.path for r in self.retrieved}
        return [f for f in gold.expected_files if f in retrieved_paths]

    def files_missed(self, gold: GoldSet) -> list[str]:
        retrieved_paths = {r.path for r in self.retrieved}
        return [f for f in gold.expected_files if f not in retrieved_paths]

    def recall(self, gold: GoldSet) -> float:
        if not gold.expected_files:
            return 1.0
        return len(self.files_hit(gold)) / len(gold.expected_files)

    def precision(self, gold: GoldSet) -> float:
        if not self.retrieved:
            return 0.0
        hits = set(self.files_hit(gold))
        return sum(1 for r in self.retrieved if r.path in hits) / len(self.retrieved)

    def to_cli_text(self) -> str:
        lines = [
            "=== Retrieval Audit Report ===",
            f"Query:    {self.query}",
            f"Provider: {self.provider} ({self.mode})",
            f"Routing:  {self.routing_decision}",
            f"Latency:  {self.latency_ms}ms",
            f"Scope:    {', '.join(self.scope_paths) or 'all'}",
            "",
            f"Retrieved ({len(self.retrieved)}):",
        ]
        for r in self.retrieved:
            ext = " [external]" if r.is_external else ""
            lines.append(f"  [{r.score:.2f}] {r.path}{ext}")
        if self.discarded:
            lines += ["", f"Discarded ({len(self.discarded)}):"]
            for d in self.discarded:
                lines.append(f"  [{d.reason.value}] {d.path}: {d.detail}")
        if self.gold_comparison:
            lines += ["", "Gold Comparison:"]
            for k, v in self.gold_comparison.items():
                lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id, "query": self.query,
            "provider": self.provider, "mode": self.mode,
            "scope_paths": self.scope_paths, "latency_ms": self.latency_ms,
            "retrieved_count": len(self.retrieved),
            "discarded_count": len(self.discarded),
            "routing_decision": self.routing_decision,
            "redactions_applied": self.redactions_applied,
            "gold_comparison": self.gold_comparison,
            "created_at": self.created_at,
        }

@dataclass
class BenchmarkComparison:
    comparison_id: str
    query: str
    gold: GoldSet | None
    results_by_mode: dict[str, RetrievalAuditReport]
    winner: str | None   # mode with highest recall
    created_at: float

    def to_markdown(self) -> str:
        lines = [f"# Retrieval Benchmark: {self.query}", ""]
        if self.gold:
            lines += [f"Gold files: {', '.join(self.gold.expected_files)}", ""]
        lines += [
            "| Mode | Retrieved | Missed | Recall | Precision | Latency |",
            "|---|---|---|---|---|---|",
        ]
        for mode, report in self.results_by_mode.items():
            if self.gold:
                r = f"{report.recall(self.gold):.0%}"
                p = f"{report.precision(self.gold):.0%}"
                missed = len(report.files_missed(self.gold))
            else:
                r = p = "N/A"
                missed = 0
            lines.append(
                f"| {mode} | {len(report.retrieved)} | {missed} | {r} | {p} | {report.latency_ms}ms |"
            )
        if self.winner:
            lines += ["", f"**Winner:** {self.winner}"]
        return "\n".join(lines)


class RetrievalBenchmarkRunner:
    def __init__(self, *, gold_sets: list[GoldSet] | None = None) -> None:
        self._gold_sets = {g.gold_id: g for g in (gold_sets or [])}

    def register_gold_set(self, gold: GoldSet) -> None:
        self._gold_sets[gold.gold_id] = gold

    def run_comparison(
        self,
        query: str,
        *,
        modes: list[RetrievalMode],
        provider_factory: Any,
        gold_id: str | None = None,
        scope_paths: list[str] | None = None,
    ) -> BenchmarkComparison:
        gold = self._gold_sets.get(gold_id) if gold_id else None
        results: dict[str, RetrievalAuditReport] = {}

        for mode in modes:
            report = self._run_mode(
                query, mode=mode, provider_factory=provider_factory,
                scope_paths=scope_paths or [], gold=gold,
            )
            results[mode.value] = report

        winner = self._pick_winner(results, gold)
        return BenchmarkComparison(
            comparison_id=str(uuid.uuid4()), query=query, gold=gold,
            results_by_mode=results, winner=winner, created_at=time.time(),
        )

    def make_audit_report(
        self,
        *,
        query: str,
        provider: str,
        mode: str,
        retrieved: list[RetrievedResult],
        discarded: list[DiscardedResult] | None = None,
        scope_paths: list[str] | None = None,
        routing_decision: str = "direct",
        latency_ms: int = 0,
        redactions_applied: int = 0,
        gold: GoldSet | None = None,
    ) -> RetrievalAuditReport:
        gold_cmp = None
        if gold:
            retrieved_paths = {r.path for r in retrieved}
            hits = [f for f in gold.expected_files if f in retrieved_paths]
            missed = [f for f in gold.expected_files if f not in retrieved_paths]
            gold_cmp = {
                "expected": len(gold.expected_files),
                "hits": len(hits),
                "missed_files": missed,
                "recall": len(hits) / max(len(gold.expected_files), 1),
            }
        return RetrievalAuditReport(
            report_id=str(uuid.uuid4()), query=query, provider=provider,
            mode=mode, scope_paths=list(scope_paths or []),
            retrieved=list(retrieved), discarded=list(discarded or []),
            routing_decision=routing_decision, redactions_applied=redactions_applied,
            latency_ms=latency_ms, created_at=time.time(), gold_comparison=gold_cmp,
        )

    def _run_mode(
        self,
        query: str,
        *,
        mode: RetrievalMode,
        provider_factory: Any,
        scope_paths: list[str],
        gold: GoldSet | None,
    ) -> RetrievalAuditReport:
        start = time.time()
        try:
            raw = provider_factory(mode.value, query)
            retrieved = [
                RetrievedResult(**r) if isinstance(r, dict) else r
                for r in (raw or [])
            ]
        except Exception:
            retrieved = []
        latency = int((time.time() - start) * 1000)
        return self.make_audit_report(
            query=query, provider=mode.value, mode=mode.value,
            retrieved=retrieved, scope_paths=scope_paths,
            routing_decision=f"benchmark:{mode.value}", latency_ms=latency, gold=gold,
        )

    def _pick_winner(
        self, results: dict[str, RetrievalAuditReport], gold: GoldSet | None
    ) -> str | None:
        if not gold or not results:
            return None
        best_mode, best_recall = None, -1.0
        for mode, report in results.items():
            r = report.recall(gold)
            if r > best_recall:
                best_recall = r
                best_mode = mode
        return best_mode
