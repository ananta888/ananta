from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


_ROLE_ORDER = {
    "entrypoint": 0,
    "route": 0,
    "api": 0,
    "service": 1,
    "orchestrator": 1,
    "worker": 1,
    "model": 2,
    "dto": 2,
    "config": 2,
    "test": 3,
    "doc": 4,
}


@dataclass(frozen=True)
class ArchitecturePlanBudget:
    max_batches: int = 8
    files_per_batch: int = 3
    max_ref_chars: int = 4000
    max_total_ref_count: int = 120
    max_summary_chars: int = 12000

    @classmethod
    def from_profile(cls, profile: dict[str, Any] | None) -> "ArchitecturePlanBudget":
        raw = dict((profile or {}).get("budgets") or {})

        def _bounded(key: str, default: int, lo: int, hi: int) -> int:
            try:
                return max(lo, min(hi, int(raw.get(key, default))))
            except (TypeError, ValueError):
                return default

        return cls(
            max_batches=_bounded("max_batches", cls.max_batches, 1, 64),
            files_per_batch=_bounded("files_per_batch", cls.files_per_batch, 1, 20),
            max_ref_chars=_bounded("max_ref_chars", cls.max_ref_chars, 500, 40_000),
            max_total_ref_count=_bounded("max_total_ref_count", cls.max_total_ref_count, 1, 500),
            max_summary_chars=_bounded("max_summary_chars", cls.max_summary_chars, 1000, 80_000),
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "max_batches": self.max_batches,
            "files_per_batch": self.files_per_batch,
            "max_ref_chars": self.max_ref_chars,
            "max_total_ref_count": self.max_total_ref_count,
            "max_summary_chars": self.max_summary_chars,
        }


class ArchitectureAnalysisPlanner:
    """Build deterministic full-scan architecture analysis plans from retrieval scope."""

    def build_plan(
        self,
        *,
        query: str,
        research_context: dict[str, Any] | None,
        retrieval_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ctx = dict(research_context or {})
        profile = dict(retrieval_profile or ctx.get("retrieval_profile") or {})
        budget = ArchitecturePlanBudget.from_profile(profile)
        coverage_policy = str(profile.get("coverage_policy") or ctx.get("coverage_policy") or "seed_only")

        refs = self._collect_refs(ctx, profile)
        relation_edges = [dict(edge or {}) for edge in list(ctx.get("relation_edges") or []) if edge]
        normalized, excluded = self._normalize_refs(refs, budget=budget)
        normalized.sort(key=self._sort_key)

        max_refs = min(len(normalized), budget.max_batches * budget.files_per_batch, budget.max_total_ref_count)
        planned_refs = normalized[:max_refs]
        for ref in normalized[max_refs:]:
            excluded.append({"ref": ref, "reason": "budget_exceeded"})

        batches: list[dict[str, Any]] = []
        for idx in range(0, len(planned_refs), budget.files_per_batch):
            batch_refs = planned_refs[idx: idx + budget.files_per_batch]
            batch_id = self._stable_id("batch", batch_refs)
            batches.append({
                "batch_id": batch_id,
                "index": len(batches) + 1,
                "refs": batch_refs,
            })

        plan_core = {
            "query": query,
            "profile_id": profile.get("profile_id"),
            "coverage_policy": coverage_policy,
            "refs": planned_refs,
            "relation_edges": relation_edges,
            "budget": budget.as_dict(),
        }
        plan_id = self._stable_id("architecture-plan", plan_core)
        return {
            "schema": "architecture_analysis_plan.v1",
            "plan_id": plan_id,
            "query": query,
            "profile_id": profile.get("profile_id"),
            "analysis_mode": profile.get("analysis_mode") or ctx.get("analysis_mode") or "architecture_full_scan",
            "output_intent": profile.get("output_intent") or ctx.get("output_intent") or "architecture_overview",
            "coverage_policy": coverage_policy,
            "summary_policy": profile.get("summary_policy") or ctx.get("summary_policy") or "rolling_structured",
            "budget": budget.as_dict(),
            "batches": batches,
            "planned_refs": planned_refs,
            "excluded_refs": excluded,
            "relation_edges": relation_edges,
            "coverage": {
                "planned_refs": len(planned_refs),
                "excluded_refs": len(excluded),
                "batch_count": len(batches),
            },
        }

    def _collect_refs(self, ctx: dict[str, Any], profile: dict[str, Any]) -> list[dict[str, Any]]:
        full_scan = str(profile.get("analysis_mode") or ctx.get("analysis_mode") or "").strip() == "architecture_full_scan"
        architecture_scope = dict(ctx.get("architecture_scope") or {})
        arch_refs = [dict(ref or {}) for ref in list(architecture_scope.get("refs") or []) if ref]
        if full_scan and arch_refs:
            return arch_refs
        return [dict(ref or {}) for ref in list(ctx.get("repo_scope_refs") or []) if ref]

    def _normalize_refs(
        self,
        refs: list[dict[str, Any]],
        *,
        budget: ArchitecturePlanBudget,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        normalized: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        seen: set[str] = set()

        for raw in refs:
            ref = dict(raw or {})
            rel_path = str(ref.get("path") or ref.get("source") or "").strip()
            chunks = [dict(chunk or {}) for chunk in list(ref.get("chunks") or []) if chunk]
            start_line = self._to_int(ref.get("start_line") or ref.get("line_start") or ref.get("from_line"))
            end_line = self._to_int(ref.get("end_line") or ref.get("line_end") or ref.get("to_line"))
            if start_line is not None and end_line is not None and end_line < start_line:
                start_line = end_line = None
            source_kind = "line_range" if rel_path and start_line is not None and end_line is not None else ("chunk" if chunks else ("file_excerpt" if rel_path else "codecompass_snippet"))
            content_hint = str(ref.get("snippet") or ref.get("content") or "")[:budget.max_ref_chars]
            dedupe = self._dedupe_key(rel_path, start_line, end_line, chunks, content_hint)
            if dedupe in seen:
                excluded.append({"ref": ref, "reason": "duplicate"})
                continue
            seen.add(dedupe)
            score = self._to_float(ref.get("score"))
            role = str(ref.get("role") or self._infer_role(rel_path)).strip() or "service"
            normalized_ref = {
                "block_id": self._stable_id("ref", dedupe),
                "rel_path": rel_path or "(codecompass_snippet)",
                "path": rel_path,
                "source_kind": source_kind,
                "start_line": start_line,
                "end_line": end_line,
                "score": score,
                "symbol": str(ref.get("symbol") or "").strip() or None,
                "reason": str(ref.get("reason") or "").strip() or None,
                "role": role,
                "component_hint": str(ref.get("component_hint") or "").strip() or None,
                "dependency_kind": str(ref.get("dependency_kind") or "").strip() or None,
                "estimated_chars": min(budget.max_ref_chars, self._estimate_chars(ref)),
                "chunks": chunks,
                "snippet": content_hint,
            }
            normalized.append(normalized_ref)
        return normalized, excluded

    def _sort_key(self, ref: dict[str, Any]) -> tuple[int, int, str]:
        role = str(ref.get("role") or "")
        score = ref.get("score")
        score_key = int(float(score or 0.0) * -1000)
        return (_ROLE_ORDER.get(role, 5), score_key, str(ref.get("rel_path") or ""))

    def _dedupe_key(
        self,
        rel_path: str,
        start_line: int | None,
        end_line: int | None,
        chunks: list[dict[str, Any]],
        content_hint: str,
    ) -> str:
        if chunks:
            digest = hashlib.sha1(json.dumps(chunks, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]
            return f"{rel_path}:chunks:{digest}"
        if content_hint and not rel_path:
            digest = hashlib.sha1(content_hint.encode("utf-8")).hexdigest()[:12]
            return f"snippet:{digest}"
        return f"{rel_path}:{start_line}:{end_line}"

    def _estimate_chars(self, ref: dict[str, Any]) -> int:
        chunks = [dict(chunk or {}) for chunk in list(ref.get("chunks") or []) if chunk]
        if chunks:
            return sum(len(str(chunk.get("content") or chunk.get("excerpt") or "")) for chunk in chunks)
        return len(str(ref.get("snippet") or ref.get("content") or "")) or 1000

    def _infer_role(self, rel_path: str) -> str:
        path = str(rel_path or "").lower()
        if "/routes/" in path or path.endswith("_route.py") or path.endswith("routes.py"):
            return "route"
        if "/services/" in path or "_service" in path:
            return "service"
        if "orchestrator" in path or "worker" in path:
            return "orchestrator"
        if "model" in path or "schema" in path:
            return "model"
        if "config" in path or path.endswith(".env"):
            return "config"
        if "/tests/" in path or path.startswith("tests/"):
            return "test"
        if path.startswith("docs/") or path.endswith(".md"):
            return "doc"
        return "service"

    def _stable_id(self, prefix: str, payload: Any) -> str:
        raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=True)
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        return f"{prefix}:{digest}"

    def _to_int(self, value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _to_float(self, value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


_architecture_analysis_planner = ArchitectureAnalysisPlanner()


def get_architecture_analysis_planner() -> ArchitectureAnalysisPlanner:
    return _architecture_analysis_planner
