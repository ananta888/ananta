from __future__ import annotations
import hashlib, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class DropReason(str, Enum):
    DENIED_PATH = "denied_path"
    DUPLICATE = "duplicate"
    STALE = "stale"
    LOW_SCORE = "low_score"
    OVER_BUDGET = "over_budget"
    REDACTED = "redacted"
    MISSING_SNIPPET = "missing_snippet"

ALWAYS_DENIED = frozenset([".env", ".git", "secrets", "node_modules", ".venv", ".augment"])

@dataclass
class RawCandidate:
    provider: str
    path: str
    snippet: str
    score: float          # 0.0-1.0
    symbol: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    source_kind: str = "unknown"
    freshness: float = 1.0
    active_status: str = "unknown"
    confidence: float = 0.5
    evidence: list[dict] | None = None

@dataclass
class DroppedCandidate:
    provider: str
    path: str
    score: float
    reason: DropReason
    step: str
    detail: str | None = None

@dataclass
class CuratedItem:
    item_id: str
    provider: str
    path: str
    snippet: str
    score: float
    symbol: str | None
    line_start: int | None
    line_end: int | None
    source_kind: str
    confidence: float
    freshness: float
    active_status: str
    policy_status: str
    evidence: list[dict]
    truncated: bool
    reason: str

@dataclass
class CurationTrace:
    trace_id: str
    query: str
    budget_chars: int
    input_count: int
    output_count: int
    dropped: list[DroppedCandidate]
    selected: list[CuratedItem]
    budget_used_chars: int
    steps_run: list[str]
    created_at: float

@dataclass
class CurationPolicy:
    allowed_paths: list[str]
    denied_paths: list[str]
    min_score: float = 0.2
    max_items: int = 15
    max_snippet_chars: int = 2000
    budget_chars: int = 40000
    stale_days: int = 90
    always_denied_paths: list[str] = field(default_factory=lambda: list(ALWAYS_DENIED))

class ContextCurationPipeline:
    def __init__(self, policy: CurationPolicy | None = None):
        self.policy = policy or CurationPolicy(allowed_paths=[], denied_paths=[])

    def curate(self, candidates: list[RawCandidate], query: str) -> CurationTrace:
        dropped: list[DroppedCandidate] = []
        steps_run: list[str] = []

        # Step 1: filter missing snippets
        active = [c for c in candidates if c.snippet.strip()]
        dropped += [DroppedCandidate(c.provider, c.path, c.score, DropReason.MISSING_SNIPPET, "step1") for c in candidates if not c.snippet.strip()]
        steps_run.append("retrieve_candidates")

        # Step 2: policy filter
        active, d2 = self._policy_filter(active)
        dropped += d2
        steps_run.append("apply_policy_filter")

        # Step 3: deduplicate
        active, d3 = self._deduplicate(active)
        dropped += d3
        steps_run.append("deduplicate")

        # Step 4: rank by relevance
        active = sorted(active, key=lambda c: c.score, reverse=True)
        steps_run.append("rank_by_relevance")

        # Step 5: rank by freshness
        active = sorted(active, key=lambda c: c.score * (0.7 + 0.3 * c.freshness), reverse=True)
        steps_run.append("rank_by_freshness")

        # Step 6: rank by active status (penalty)
        STATUS_MULT = {"dead_candidate": 0.3, "deprecated": 0.6}
        active = sorted(active, key=lambda c: c.score * STATUS_MULT.get(c.active_status, 1.0), reverse=True)
        steps_run.append("rank_by_active_status")

        # Step 7: compress snippets
        for c in active:
            if len(c.snippet) > self.policy.max_snippet_chars:
                c.snippet = c.snippet[:self.policy.max_snippet_chars]
                c.evidence = c.evidence or []  # mark truncated via CuratedItem
        steps_run.append("compress_snippets")

        # Step 8: attach evidence
        for c in active:
            if not c.evidence:
                c.evidence = [{"type": "heuristic", "source": c.path}]
        steps_run.append("attach_evidence")

        # Step 9: fit context budget
        active, d9 = self._fit_budget(active)
        dropped += d9
        steps_run.append("fit_context_budget")

        # Step 10: emit trace
        selected = [self._build_item(c) for c in active]
        budget_used = sum(len(item.snippet) for item in selected)
        steps_run.append("emit_trace")

        return CurationTrace(
            trace_id=str(uuid.uuid4()),
            query=query,
            budget_chars=self.policy.budget_chars,
            input_count=len(candidates),
            output_count=len(selected),
            dropped=dropped,
            selected=selected,
            budget_used_chars=budget_used,
            steps_run=steps_run,
            created_at=time.time(),
        )

    def _policy_filter(self, candidates: list[RawCandidate]) -> tuple[list[RawCandidate], list[DroppedCandidate]]:
        ok, dropped = [], []
        for c in candidates:
            if any(denied in c.path for denied in self.policy.always_denied_paths + self.policy.denied_paths):
                dropped.append(DroppedCandidate(c.provider, c.path, c.score, DropReason.DENIED_PATH, "step2"))
                continue
            if c.score < self.policy.min_score:
                dropped.append(DroppedCandidate(c.provider, c.path, c.score, DropReason.LOW_SCORE, "step2", f"score {c.score} < {self.policy.min_score}"))
                continue
            ok.append(c)
        return ok, dropped

    def _deduplicate(self, candidates: list[RawCandidate]) -> tuple[list[RawCandidate], list[DroppedCandidate]]:
        seen: dict[str, RawCandidate] = {}
        dropped = []
        for c in candidates:
            key = f"{c.provider}:{c.path}:{c.line_start}"
            if key in seen:
                if c.score > seen[key].score:
                    dropped.append(DroppedCandidate(seen[key].provider, seen[key].path, seen[key].score, DropReason.DUPLICATE, "step3"))
                    seen[key] = c
                else:
                    dropped.append(DroppedCandidate(c.provider, c.path, c.score, DropReason.DUPLICATE, "step3"))
            else:
                seen[key] = c
        return list(seen.values()), dropped

    def _fit_budget(self, candidates: list[RawCandidate]) -> tuple[list[RawCandidate], list[DroppedCandidate]]:
        ok, dropped = [], []
        used = 0
        for c in candidates:
            if used + len(c.snippet) <= self.policy.budget_chars and len(ok) < self.policy.max_items:
                ok.append(c)
                used += len(c.snippet)
            else:
                dropped.append(DroppedCandidate(c.provider, c.path, c.score, DropReason.OVER_BUDGET, "step9"))
        return ok, dropped

    def _build_item(self, c: RawCandidate) -> CuratedItem:
        raw = f"{c.provider}{c.path}{c.line_start or ''}"
        item_id = hashlib.sha256(raw.encode()).hexdigest()[:16]
        truncated = len(c.snippet) >= self.policy.max_snippet_chars
        return CuratedItem(
            item_id=item_id, provider=c.provider, path=c.path, snippet=c.snippet,
            score=c.score, symbol=c.symbol, line_start=c.line_start, line_end=c.line_end,
            source_kind=c.source_kind, confidence=c.confidence, freshness=c.freshness,
            active_status=c.active_status, policy_status="allowed",
            evidence=list(c.evidence or []), truncated=truncated,
            reason=f"score={c.score:.2f}, source={c.source_kind}",
        )
