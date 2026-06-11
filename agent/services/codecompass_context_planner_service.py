from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


SCHEMA_LOCATION_REF = "codecompass_location_ref.v1"
SCHEMA_CONTEXT_BUNDLE = "codecompass_context_bundle.v1"


@dataclass(frozen=True)
class CodeCompassContextBudget:
    max_ranges: int = 8
    max_lines_per_range: int = 120
    max_neighbors: int = 6

    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None = None) -> "CodeCompassContextBudget":
        data = dict(raw or {})

        def _int(name: str, default: int, lo: int, hi: int) -> int:
            try:
                return max(lo, min(int(data.get(name, default)), hi))
            except (TypeError, ValueError):
                return default

        return cls(
            max_ranges=_int("max_ranges", cls.max_ranges, 1, 40),
            max_lines_per_range=_int("max_lines_per_range", cls.max_lines_per_range, 1, 400),
            max_neighbors=_int("max_neighbors", cls.max_neighbors, 0, 30),
        )


class CodeCompassContextPlanner:
    """Build bounded path+range context bundles from CodeCompass retrieval data."""

    def plan_context(
        self,
        *,
        query: str,
        task_kind: str | None = None,
        budget: dict[str, Any] | None = None,
        workspace_dir: str | None = None,
        include_neighbors: bool = True,
    ) -> dict[str, Any]:
        effective = CodeCompassContextBudget.from_raw(budget)
        search_refs, warnings = self._search_refs(query=query, max_ranges=effective.max_ranges * 3)
        refs = list(search_refs)
        if include_neighbors:
            refs.extend(self._neighbor_refs(search_refs[: effective.max_neighbors]))
        selected, excluded = self._budget_refs(refs, budget=effective)
        patch_targets = [self._patch_target(ref) for ref in selected]
        bundle_core = {
            "query": str(query or ""),
            "task_kind": str(task_kind or ""),
            "location_refs": selected,
            "patch_targets": patch_targets,
        }
        return {
            "schema": SCHEMA_CONTEXT_BUNDLE,
            "bundle_id": self._stable_id("cc-bundle", bundle_core),
            "query": str(query or ""),
            "task_kind": str(task_kind or ""),
            "location_refs": selected,
            "patch_targets": patch_targets,
            "excluded_refs": excluded,
            "budget": {
                "max_ranges": effective.max_ranges,
                "max_lines_per_range": effective.max_lines_per_range,
                "max_neighbors": effective.max_neighbors,
            },
            "diagnostics": {
                "range_count": len(selected),
                "excluded_count": len(excluded),
                "bounded": True,
                "workspace_dir": str(workspace_dir or ""),
            },
            "warnings": sorted(set(warnings)),
        }

    def location_ref_from_hit(self, hit: dict[str, Any], *, reason: str = "codecompass.search") -> dict[str, Any] | None:
        raw = dict(hit or {})
        metadata = dict(raw.get("metadata") or {})
        path = str(raw.get("path") or raw.get("source") or metadata.get("path") or metadata.get("file") or "").strip()
        if not path:
            return None
        line_start = self._to_int(
            raw.get("line_start") or raw.get("start_line") or metadata.get("line_start") or metadata.get("start_line")
        )
        line_end = self._to_int(raw.get("line_end") or raw.get("end_line") or metadata.get("line_end") or metadata.get("end_line"))
        if line_start is None or line_end is None or line_end < line_start:
            return None
        symbol = str(raw.get("symbol") or metadata.get("symbol") or raw.get("name") or "").strip()
        return self._location_ref(
            path=path,
            line_start=line_start,
            line_end=line_end,
            symbol=symbol or None,
            reason=reason,
            score=self._to_float(raw.get("score")),
            source=str(raw.get("source_system") or raw.get("source_type") or "codecompass"),
            node_id=str(raw.get("node_id") or raw.get("id") or "").strip() or None,
        )

    def location_ref_from_node(self, node: dict[str, Any], *, reason: str = "codecompass.graph") -> dict[str, Any] | None:
        raw = dict(node or {})
        source_record = dict(raw.get("source_record") or {})
        merged = {**source_record, **raw}
        path = str(merged.get("file") or merged.get("path") or "").strip()
        if not path:
            return None
        line_start = self._to_int(
            merged.get("line_start") or merged.get("start_line") or merged.get("from_line")
        )
        line_end = self._to_int(merged.get("line_end") or merged.get("end_line") or merged.get("to_line"))
        if line_start is None:
            line_start = 1
        if line_end is None:
            line_end = min(line_start + 79, line_start + 119)
        if line_end < line_start:
            return None
        return self._location_ref(
            path=path,
            line_start=line_start,
            line_end=line_end,
            symbol=str(merged.get("name") or merged.get("symbol") or "").strip() or None,
            reason=reason,
            score=self._to_float(merged.get("score")),
            source="codecompass_graph",
            node_id=str(merged.get("id") or merged.get("node_id") or "").strip() or None,
        )

    def _search_refs(self, *, query: str, max_ranges: int) -> tuple[list[dict[str, Any]], list[str]]:
        try:
            from agent.services.rag_helper_index_service import get_rag_helper_index_service

            hits = get_rag_helper_index_service().retrieve(profile=None, query=query, limit=max_ranges)
        except Exception as exc:
            return [], [f"codecompass_search_unavailable:{exc}"]
        refs = []
        for hit in list(hits or []):
            ref = self.location_ref_from_hit(dict(hit or {}))
            if ref is not None:
                refs.append(ref)
        return refs, ([] if refs else ["no_location_refs_from_search"])

    def _neighbor_refs(self, seeds: list[dict[str, Any]]) -> list[dict[str, Any]]:
        node_ids = [str(ref.get("node_id") or "").strip() for ref in seeds if str(ref.get("node_id") or "").strip()]
        if not node_ids:
            return []
        try:
            from agent.services.tools.codecompass_tools import _resolve_graph_store
            from worker.retrieval.codecompass_graph_expansion import expand_codecompass_graph

            store, _ = _resolve_graph_store({})
            if store is None:
                return []
            expansion = expand_codecompass_graph(store=store, seed_node_ids=node_ids, profile="bugfix_local")
        except Exception:
            return []
        refs: list[dict[str, Any]] = []
        for node in list(expansion.get("nodes") or []):
            ref = self.location_ref_from_node(dict(node), reason="codecompass.neighbor")
            if ref is not None:
                refs.append(ref)
        return refs

    def _budget_refs(
        self,
        refs: list[dict[str, Any]],
        *,
        budget: CodeCompassContextBudget,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        selected: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        seen: set[str] = set()
        for ref in sorted(refs, key=self._sort_key):
            key = f"{ref.get('path')}:{ref.get('line_start')}:{ref.get('line_end')}"
            if key in seen:
                excluded.append({"ref": ref, "reason": "duplicate"})
                continue
            seen.add(key)
            if int(ref["line_end"]) - int(ref["line_start"]) + 1 > budget.max_lines_per_range:
                ref = {**ref, "line_end": int(ref["line_start"]) + budget.max_lines_per_range - 1}
            if len(selected) >= budget.max_ranges:
                excluded.append({"ref": ref, "reason": "range_budget_exceeded"})
                continue
            selected.append(ref)
        return selected, excluded

    def _location_ref(
        self,
        *,
        path: str,
        line_start: int,
        line_end: int,
        symbol: str | None,
        reason: str,
        score: float | None,
        source: str,
        node_id: str | None,
    ) -> dict[str, Any]:
        payload = {
            "schema": SCHEMA_LOCATION_REF,
            "path": path,
            "line_start": int(line_start),
            "line_end": int(line_end),
            "symbol": symbol,
            "reason": reason,
            "score": score,
            "source": source,
            "node_id": node_id,
        }
        payload["location_id"] = self._stable_id("loc", payload)
        return payload

    def _patch_target(self, ref: dict[str, Any]) -> dict[str, Any]:
        return {
            "path": ref["path"],
            "line_start": ref["line_start"],
            "line_end": ref["line_end"],
            "reason": ref.get("reason") or "context_ref",
            "preferred_variant": "replace_range",
        }

    def _sort_key(self, ref: dict[str, Any]) -> tuple[int, str, int]:
        score = ref.get("score")
        score_key = int(float(score or 0.0) * -1000)
        return (score_key, str(ref.get("path") or ""), int(ref.get("line_start") or 0))

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


_codecompass_context_planner = CodeCompassContextPlanner()


def get_codecompass_context_planner() -> CodeCompassContextPlanner:
    return _codecompass_context_planner
