from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Optional, Dict, List

from agent.services.codecompass_editor_context_contract import (
    CodeCompassEditorDetailLevel,
    CodeCompassEditorQueryInput,
)
from agent.services.codecompass_architecture_budget import ArchitectureBudgetPolicy

SCHEMA_LOCATION_REF = "codecompass_location_ref.v1"
SCHEMA_CONTEXT_BUNDLE = "codecompass_context_bundle.v1"
SCHEMA_UNIFIED_CONTEXT = "codecompass_unified_context.v1"
SCHEMA_EDITOR_CONTEXT_BUNDLE = "codecompass_editor_context_bundle.v1"
SCHEMA_ARCHITECTURE_PREFILL = "codecompass_architecture_prefill.v1"

_VERIFICATION_RANK = {"verified": 0, "unverified": 1, "failed": 2}
_TRUST_RANK = {
    "deterministic": 0,
    "extracted": 1,
    "manual": 2,
    "declared": 3,
    "inferred": 4,
    "ambiguous": 5,
}


# COMBO-001: bucket weights per task_kind. Keys are the supported
# task kinds; values are dicts mapping bucket name -> weight.
# Buckets are described in the public docs/architecture/code-review-graph-adapter.md
# and docs/architecture/repository-intelligence-graph.md.
TASK_KIND_WEIGHTS: dict[str, dict[str, float]] = {
    "review":         {"changed_files": 0.10, "symbol_neighbors": 0.40,
                       "build_test_evidence": 0.10, "semantic_chunks": 0.30,
                       "policy_evidence": 0.10},
    "bugfix":         {"changed_files": 0.10, "symbol_neighbors": 0.30,
                       "build_test_evidence": 0.20, "semantic_chunks": 0.30,
                       "policy_evidence": 0.10},
    "ci":             {"changed_files": 0.05, "symbol_neighbors": 0.15,
                       "build_test_evidence": 0.55, "semantic_chunks": 0.15,
                       "policy_evidence": 0.10},
    "build":          {"changed_files": 0.05, "symbol_neighbors": 0.15,
                       "build_test_evidence": 0.55, "semantic_chunks": 0.15,
                       "policy_evidence": 0.10},
    "architecture_question": {"changed_files": 0.05, "symbol_neighbors": 0.30,
                              "build_test_evidence": 0.20, "semantic_chunks": 0.35,
                              "policy_evidence": 0.10},
    "security_policy_task":  {"changed_files": 0.05, "symbol_neighbors": 0.20,
                              "build_test_evidence": 0.15, "semantic_chunks": 0.20,
                              "policy_evidence": 0.40},
}

DEFAULT_WEIGHTS: dict[str, float] = {
    "changed_files": 0.10, "symbol_neighbors": 0.30,
    "build_test_evidence": 0.20, "semantic_chunks": 0.30,
    "policy_evidence": 0.10,
}


@dataclass(frozen=True)
class CodeCompassContextBudget:
    max_ranges: int = 8
    max_lines_per_range: int = 120
    max_neighbors: int = 6
    max_evidence_items: int = 12
    max_tokens: int = 12_000

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
            max_evidence_items=_int(
                "max_evidence_items",
                cls.max_evidence_items,
                1,
                100,
            ),
            max_tokens=_int("max_tokens", cls.max_tokens, 1, 200_000),
        )


class CodeCompassContextPlanner:
    """Build bounded path+range context bundles from CodeCompass retrieval data."""

    def __init__(self, *, retrieval_service: Any | None = None) -> None:
        self._retrieval_service = retrieval_service

    def _retrieval(self) -> Any:
        if self._retrieval_service is not None:
            return self._retrieval_service
        from agent.services.knowledge_index_retrieval_service import (
            get_knowledge_index_retrieval_service,
        )

        return get_knowledge_index_retrieval_service()

    @staticmethod
    def build_editor_query(
        query_input: CodeCompassEditorQueryInput | dict[str, Any],
    ) -> CodeCompassEditorQueryInput:
        """Validate one editor query without reading repository content."""

        if isinstance(query_input, CodeCompassEditorQueryInput):
            return query_input
        return CodeCompassEditorQueryInput.from_mapping(query_input)

    @staticmethod
    def editor_budget(
        detail_level: CodeCompassEditorDetailLevel | str,
    ) -> CodeCompassContextBudget:
        """Use the same selected/conversation policy as prompt composition."""

        level = (
            detail_level
            if isinstance(detail_level, CodeCompassEditorDetailLevel)
            else CodeCompassEditorDetailLevel(str(detail_level or "").strip().lower())
        )
        if level == CodeCompassEditorDetailLevel.preview:
            return CodeCompassContextBudget(
                max_ranges=0,
                max_lines_per_range=0,
                max_neighbors=0,
                max_evidence_items=0,
                max_tokens=0,
            )
        from agent.services.visual_process_context_service import (
            VisualProcessContextService,
        )

        prompt_budget = VisualProcessContextService.context_budget(level.value)
        return CodeCompassContextBudget(
            max_ranges=prompt_budget.max_ranges,
            max_lines_per_range=prompt_budget.max_lines_per_range,
            max_neighbors=4 if level == CodeCompassEditorDetailLevel.selected else 6,
            max_evidence_items=prompt_budget.max_evidence_items,
            max_tokens=prompt_budget.max_prompt_tokens,
        )

    def plan_editor_context(
        self,
        *,
        query_input: CodeCompassEditorQueryInput | dict[str, Any],
        workspace_dir: str | None = None,
        include_neighbors: bool = True,
    ) -> dict[str, Any]:
        """Plan bounded editor evidence from the canonical production Search port.

        Preview is deliberately metadata-only: it never resolves the retrieval
        dependency, reads repository content, expands a graph or invokes an LLM.
        Selected/conversation profiles share the prompt service's operational
        range, line, evidence and token caps.
        """

        contract = self.build_editor_query(query_input)
        budget = self.editor_budget(contract.detail_level)
        query_text = contract.retrieval_query()
        refs: list[dict[str, Any]] = []
        discarded: list[dict[str, Any]] = []
        warnings: list[str] = []
        retrieval_calls = 0
        graph_expansion_calls = 0
        if contract.detail_level != CodeCompassEditorDetailLevel.preview:
            retrieval_calls = 1
            try:
                hits = self._retrieval().search_records(
                    query_text,
                    limit=max(1, budget.max_evidence_items * 4),
                    task_kind="analysis",
                    retrieval_intent=contract.intent.value,
                )
            except Exception as exc:
                hits = []
                warnings.append(f"codecompass_search_unavailable:{type(exc).__name__}")
            for hit in list(hits or []):
                if not isinstance(hit, dict):
                    discarded.append(self._discard_stub({}, "candidate_invalid"))
                    continue
                ref = self.location_ref_from_hit(
                    hit,
                    reason=f"codecompass.editor.{contract.intent.value}",
                )
                if ref is None:
                    discarded.append(self._discard_stub(hit, "location_range_invalid"))
                    continue
                refs.append(ref)
            if include_neighbors and refs and budget.max_neighbors:
                graph_expansion_calls = 1
                stable_seeds = sorted(refs, key=self._sort_key)[: budget.max_neighbors]
                refs.extend(self._neighbor_refs(stable_seeds))
        else:
            warnings.append("preview_metadata_only")

        invalid_discarded = list(discarded)
        selected, budget_discarded, budget_trace, estimated_tokens = self._budget_editor_refs(
            refs,
            budget=budget,
        )
        discarded.extend(budget_discarded)
        discarded.sort(key=self._discard_sort_key)
        invalid_trace = [
            {
                **self._trace_identity(row),
                "decision": "discarded",
                "reason": str(row.get("reason") or "candidate_invalid"),
                "estimated_tokens": 0,
                "cumulative_tokens": 0,
            }
            for row in sorted(invalid_discarded, key=self._discard_sort_key)
        ]
        budget_trace = invalid_trace + budget_trace
        patch_targets = [self._patch_target(ref) for ref in selected]
        budget_payload = {
            "profile": contract.detail_level.value,
            "max_ranges": budget.max_ranges,
            "max_lines_per_range": budget.max_lines_per_range,
            "max_neighbors": budget.max_neighbors,
            "max_evidence_items": budget.max_evidence_items,
            "max_tokens": budget.max_tokens,
        }
        bundle_core = {
            "schema": SCHEMA_EDITOR_CONTEXT_BUNDLE,
            "query_input": contract.as_dict(),
            "query_text": query_text,
            "location_refs": selected,
            "patch_targets": patch_targets,
            "discarded": discarded,
            "budget": budget_payload,
            "budget_trace": budget_trace,
            "budget_usage": {
                "ranges": sum(ref.get("line_start") is not None for ref in selected),
                "evidence_items": len(selected),
                "estimated_tokens": estimated_tokens,
            },
        }
        bundle_id = self._stable_sha256_id("cc-editor", bundle_core)
        return {
            **bundle_core,
            "bundle_id": bundle_id,
            # Compatibility for the original range planner consumers.
            "excluded_refs": list(discarded),
            "diagnostics": {
                "bounded": True,
                "workspace_dir_provided": bool(str(workspace_dir or "").strip()),
                "retrieval_calls": retrieval_calls,
                "graph_expansion_calls": graph_expansion_calls,
                "repository_content_reads": 0,
                "llm_calls": 0,
                "selected_count": len(selected),
                "discarded_count": len(discarded),
            },
            "warnings": sorted(set(warnings or ([] if selected else ["no_location_refs_from_search"]))),
        }

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

    def location_ref_from_hit(
        self,
        hit: dict[str, Any],
        *,
        reason: str = "codecompass.search",
    ) -> dict[str, Any] | None:
        raw = dict(hit or {})
        metadata = dict(raw.get("metadata") or {})
        path = self._bounded_text(
            raw.get("path") or raw.get("source") or metadata.get("path") or metadata.get("file"),
            1_000,
        )
        if not path:
            return None
        line_start = self._to_int(
            raw.get("line_start") or raw.get("start_line") or metadata.get("line_start") or metadata.get("start_line")
        )
        line_end = self._to_int(
            raw.get("line_end")
            or raw.get("end_line")
            or metadata.get("line_end")
            or metadata.get("end_line")
        )
        if line_start is None or line_end is None or line_start < 1 or line_end < line_start:
            return None
        symbol = self._bounded_text(
            raw.get("symbol") or metadata.get("symbol") or raw.get("name"),
            300,
        )
        verification_status = self._verification_status(
            raw.get("verification_status")
            or metadata.get("verification_status")
            or (metadata.get("source_id_verification") or {}).get("status")
        )
        trust_level = self._trust_level(
            raw.get("trust_level") or metadata.get("trust_level")
        )
        record_id = self._bounded_text(
            raw.get("record_id")
            or raw.get("id")
            or metadata.get("record_id")
            or "",
            300,
        )
        return self._location_ref(
            path=path,
            line_start=line_start,
            line_end=line_end,
            symbol=symbol or None,
            reason=reason,
            score=self._to_float(raw.get("score")),
            source=self._bounded_text(
                raw.get("source_system") or raw.get("source_type") or "codecompass",
                160,
            ),
            node_id=self._bounded_text(raw.get("node_id") or raw.get("id"), 300) or None,
            record_id=record_id or None,
            verification_status=verification_status,
            trust_level=trust_level,
            estimated_tokens=self._candidate_token_estimate(raw, metadata),
        )

    def location_ref_from_node(
        self,
        node: dict[str, Any],
        *,
        reason: str = "codecompass.graph",
    ) -> dict[str, Any] | None:
        raw = dict(node or {})
        source_record = dict(raw.get("source_record") or {})
        merged = {**source_record, **raw}
        path = self._bounded_text(merged.get("file") or merged.get("path"), 1_000)
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
        if line_start < 1 or line_end < line_start:
            return None
        return self._location_ref(
            path=path,
            line_start=line_start,
            line_end=line_end,
            symbol=self._bounded_text(merged.get("name") or merged.get("symbol"), 300) or None,
            reason=reason,
            score=self._to_float(merged.get("score")),
            source="codecompass_graph",
            node_id=self._bounded_text(merged.get("id") or merged.get("node_id"), 300) or None,
            record_id=self._bounded_text(merged.get("record_id") or merged.get("id"), 300) or None,
            verification_status=self._verification_status(merged.get("verification_status")),
            trust_level=self._trust_level(merged.get("trust_level")),
            estimated_tokens=self._candidate_token_estimate(merged, source_record),
        )

    def _search_refs(self, *, query: str, max_ranges: int) -> tuple[list[dict[str, Any]], list[str]]:
        try:
            hits = self._retrieval().search_records(
                query,
                limit=max_ranges,
                retrieval_intent="exact_symbol",
            )
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
            from ananta_codecompass.graph_expansion import expand_codecompass_graph

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

    def _budget_editor_refs(
        self,
        refs: list[dict[str, Any]],
        *,
        budget: CodeCompassContextBudget,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
        selected: list[dict[str, Any]] = []
        discarded: list[dict[str, Any]] = []
        trace: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        ranges = 0
        token_usage = 0
        for ref in sorted(refs, key=self._sort_key):
            identity = self._dedupe_key(ref)
            audit = self._trace_identity(ref)
            if identity in seen:
                row = self._discard_stub(ref, "duplicate_evidence")
                discarded.append(row)
                trace.append(
                    {
                        **audit,
                        "decision": "discarded",
                        "reason": "duplicate_evidence",
                        "estimated_tokens": max(1, int(ref.get("estimated_tokens") or 1)),
                        "cumulative_tokens": token_usage,
                    }
                )
                continue
            seen.add(identity)
            bounded = dict(ref)
            has_range = bounded.get("line_start") is not None
            if has_range and ranges >= budget.max_ranges:
                row = self._discard_stub(bounded, "range_budget_exceeded")
                discarded.append(row)
                trace.append(
                    {
                        **audit,
                        "decision": "discarded",
                        "reason": "range_budget_exceeded",
                        "estimated_tokens": max(1, int(bounded.get("estimated_tokens") or 1)),
                        "cumulative_tokens": token_usage,
                    }
                )
                continue
            truncated = False
            if has_range:
                line_start = int(bounded["line_start"])
                line_end = int(bounded.get("line_end") or line_start)
                line_count = line_end - line_start + 1
                if line_count > budget.max_lines_per_range:
                    bounded["line_end"] = line_start + budget.max_lines_per_range - 1
                    original_tokens = max(1, int(bounded.get("estimated_tokens") or 1))
                    bounded["estimated_tokens"] = max(
                        1,
                        math.ceil(original_tokens * budget.max_lines_per_range / line_count),
                    )
                    truncated = True
            candidate_tokens = max(1, int(bounded.get("estimated_tokens") or 1))
            if token_usage + candidate_tokens > budget.max_tokens:
                row = self._discard_stub(bounded, "token_budget_exceeded")
                discarded.append(row)
                trace.append(
                    {
                        **audit,
                        "decision": "discarded",
                        "reason": "token_budget_exceeded",
                        "estimated_tokens": candidate_tokens,
                        "cumulative_tokens": token_usage,
                    }
                )
                continue
            if len(selected) >= budget.max_evidence_items:
                row = self._discard_stub(bounded, "evidence_item_budget_exceeded")
                discarded.append(row)
                trace.append(
                    {
                        **audit,
                        "decision": "discarded",
                        "reason": "evidence_item_budget_exceeded",
                        "estimated_tokens": candidate_tokens,
                        "cumulative_tokens": token_usage,
                    }
                )
                continue
            if has_range:
                ranges += 1
            token_usage += candidate_tokens
            bounded["estimated_tokens"] = candidate_tokens
            selected.append(bounded)
            trace.append(
                {
                    **self._trace_identity(bounded),
                    "decision": "selected",
                    "reason": "line_budget_truncated" if truncated else "within_budget",
                    "estimated_tokens": candidate_tokens,
                    "cumulative_tokens": token_usage,
                }
            )
        return selected, discarded, trace, token_usage

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
        record_id: str | None,
        verification_status: str,
        trust_level: str,
        estimated_tokens: int,
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
            "record_id": record_id,
            "verification_status": verification_status,
            "trust_level": trust_level,
            "estimated_tokens": max(1, int(estimated_tokens)),
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

    def _sort_key(
        self,
        ref: dict[str, Any],
    ) -> tuple[int, int, float, str, int, str, int, str]:
        verification = self._verification_status(ref.get("verification_status"))
        trust = self._trust_level(ref.get("trust_level"))
        return (
            _VERIFICATION_RANK[verification],
            _TRUST_RANK[trust],
            -float(ref.get("score") or 0.0),
            str(ref.get("path") or ""),
            int(ref.get("line_start") or 0),
            str(ref.get("record_id") or ""),
            int(ref.get("line_end") or 0),
            json.dumps(ref, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )

    def _stable_id(self, prefix: str, payload: Any) -> str:
        raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=True)
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        return f"{prefix}:{digest}"

    @staticmethod
    def _stable_sha256_id(prefix: str, payload: Any) -> str:
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return f"{prefix}-sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"

    @staticmethod
    def _verification_status(value: Any) -> str:
        normalized = str(value or "unverified").strip().lower()
        return normalized if normalized in _VERIFICATION_RANK else "unverified"

    @staticmethod
    def _trust_level(value: Any) -> str:
        normalized = str(value or "inferred").strip().lower()
        return normalized if normalized in _TRUST_RANK else "ambiguous"

    @staticmethod
    def _candidate_token_estimate(
        raw: dict[str, Any],
        metadata: dict[str, Any],
    ) -> int:
        supplied = raw.get("estimated_tokens", metadata.get("estimated_tokens"))
        if isinstance(supplied, int) and not isinstance(supplied, bool) and supplied > 0:
            return min(supplied, 1_000_000)
        content = str(
            raw.get("content")
            or raw.get("text")
            or raw.get("snippet")
            or metadata.get("content")
            or ""
        )
        if content:
            return max(1, math.ceil(len(content) / 4))
        line_start = CodeCompassContextPlanner._to_int(raw.get("line_start") or metadata.get("line_start"))
        line_end = CodeCompassContextPlanner._to_int(raw.get("line_end") or metadata.get("line_end"))
        if line_start is not None and line_end is not None and line_end >= line_start:
            return max(1, (line_end - line_start + 1) * 8)
        return 1

    @staticmethod
    def _dedupe_key(ref: dict[str, Any]) -> tuple[Any, ...]:
        record_id = str(ref.get("record_id") or "").strip()
        if record_id:
            return ("record", record_id)
        return (
            "location",
            str(ref.get("path") or ""),
            int(ref.get("line_start") or 0),
            int(ref.get("line_end") or 0),
            str(ref.get("symbol") or ""),
        )

    @staticmethod
    def _trace_identity(ref: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(ref.get("metadata") or {}) if isinstance(ref.get("metadata"), dict) else {}
        return {
            "record_id": CodeCompassContextPlanner._bounded_text(
                ref.get("record_id") or ref.get("id") or ref.get("node_id"),
                300,
            )
            or None,
            "path": CodeCompassContextPlanner._bounded_text(
                ref.get("path")
                or ref.get("source")
                or ref.get("file")
                or metadata.get("path")
                or metadata.get("file"),
                1_000,
            )
            or None,
            "line_start": CodeCompassContextPlanner._to_int(
                ref.get("line_start") or ref.get("start_line") or metadata.get("line_start")
            ),
            "line_end": CodeCompassContextPlanner._to_int(
                ref.get("line_end") or ref.get("end_line") or metadata.get("line_end")
            ),
        }

    @classmethod
    def _discard_stub(cls, ref: dict[str, Any], reason: str) -> dict[str, Any]:
        return {**cls._trace_identity(ref), "reason": str(reason)}

    @staticmethod
    def _discard_sort_key(row: dict[str, Any]) -> tuple[str, str, int, int, str]:
        return (
            str(row.get("reason") or ""),
            str(row.get("path") or ""),
            int(row.get("line_start") or 0),
            int(row.get("line_end") or 0),
            str(row.get("record_id") or ""),
        )

    @staticmethod
    def _to_int(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, float) and not value.is_integer():
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            normalized = float(value)
            return normalized if math.isfinite(normalized) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _bounded_text(value: Any, maximum: int) -> str:
        return str(value or "").strip()[: max(0, int(maximum))]

    # ------------------------------------------------------------------
    # COMBO-001: unified context planner
    # ------------------------------------------------------------------

    def weights_for(self, task_kind: str | None) -> dict[str, float]:
        """Return the bucket-weight table for a given task_kind.

        Unknown task_kinds fall back to :data:`DEFAULT_WEIGHTS`.
        """
        if not task_kind:
            return dict(DEFAULT_WEIGHTS)
        return dict(TASK_KIND_WEIGHTS.get(str(task_kind), DEFAULT_WEIGHTS))

    def plan_unified_context(
        self,
        *,
        query: str,
        task_kind: str | None = None,
        budget: dict[str, Any] | None = None,
        workspace_dir: str | None = None,
        bucket_inputs: dict[str, list[dict[str, Any]]] | None = None,
        include_neighbors: bool = True,
    ) -> dict[str, Any]:
        """Build a unified context package with explicit buckets.

        Unlike :meth:`plan_context` (which produces a flat
        ``location_refs`` list), this method:

        * accepts pre-collected inputs per bucket
        * applies the task_kind-specific weight table
        * bounds each bucket by ``bucket_max``
        * records the *reasons* for budget decisions in
          ``decisions[]``
        * never invents synthetic IDs (AGENTS.md source-grounded)

        Parameters
        ----------
        query, task_kind, budget, workspace_dir, include_neighbors
            Forwarded to :meth:`plan_context` when bucket_inputs is
            empty (so callers get the same behaviour as the legacy
            plan_context for the symbolgraph+search path).
        bucket_inputs
            ``{"changed_files": [...], "symbol_neighbors": [...],
               "build_test_evidence": [...], "semantic_chunks": [...],
               "policy_evidence": [...]}``. Each item is a
            ``location_ref``-shaped dict. Unknown buckets are recorded
            as warnings.
        """
        effective = CodeCompassContextBudget.from_raw(budget)
        weights = self.weights_for(task_kind)

        buckets: dict[str, list[dict[str, Any]]] = {}
        decisions: list[dict[str, Any]] = []
        warnings: list[str] = []

        raw_inputs = bucket_inputs or {}
        for bucket_name in ("changed_files", "symbol_neighbors",
                            "build_test_evidence", "semantic_chunks",
                            "policy_evidence"):
            items = list(raw_inputs.get(bucket_name) or [])
            bounded = items[: effective.max_ranges]
            if len(items) > len(bounded):
                decisions.append({
                    "bucket": bucket_name,
                    "reason": "bucket_max_applied",
                    "weight": weights.get(bucket_name, 0.0),
                    "input_count": len(items),
                    "selected_count": len(bounded),
                })
            buckets[bucket_name] = bounded

        for bucket_name in raw_inputs:
            if bucket_name not in buckets:
                warnings.append(f"unknown_bucket:{bucket_name}")

        if not bucket_inputs:
            # Fall back to the legacy plan_context behaviour so callers
            # that only pass query/task_kind keep working.
            legacy = self.plan_context(query=query, task_kind=task_kind,
                                       budget=budget,
                                       workspace_dir=workspace_dir,
                                       include_neighbors=include_neighbors)
            buckets["symbol_neighbors"] = legacy.get("location_refs", [])
            decisions.append({
                "bucket": "symbol_neighbors",
                "reason": "legacy_plan_context_fallback",
                "weight": weights.get("symbol_neighbors", 0.0),
                "input_count": len(buckets["symbol_neighbors"]),
                "selected_count": len(buckets["symbol_neighbors"]),
            })
            warnings.extend(legacy.get("warnings") or [])

        core = {
            "schema": SCHEMA_UNIFIED_CONTEXT,
            "query": str(query or ""),
            "task_kind": str(task_kind or ""),
            "buckets": buckets,
            "weights": weights,
            "decisions": decisions,
            "diagnostics": {
                "bucket_counts": {k: len(v) for k, v in buckets.items()},
                "weighted_total": round(
                    sum(weights.get(k, 0.0) * len(v)
                        for k, v in buckets.items()), 4),
                "workspace_dir": str(workspace_dir or ""),
            },
            "warnings": sorted(set(warnings)),
        }
        core["bundle_id"] = self._stable_id("cc-unified", core)
        return core

    def plan_architecture_prefill(
        self,
        *,
        query: str,
        scope: str,
        revision: str,
        tenant: str,
        budget: Optional[Dict[str, Any]] = None,
        architecture_service: Optional[Any] = None,
        include_architecture: bool = True,
    ) -> Dict[str, Any]:
        """Build context with optional architecture prefill.
        
        This method extends plan_context to optionally include a hierarchical
        architecture slice before the fine-grained evidence snippets.
        
        Parameters
        ----------
        query
            User query or task description
        scope
            Root scope for architecture projection
        revision
            Repository revision
        tenant
            Tenant identifier
        budget
            Context budget constraints
        architecture_service
            Architecture slice service instance
        include_architecture
            Whether to include architecture prefill (intent-dependent)
            
        Returns
        -------
        dict
            Context bundle with optional architecture_context block
        """
        # Plan regular context
        regular_context = self.plan_context(
            query=query,
            budget=budget,
            include_neighbors=True
        )
        
        # Add architecture prefill if requested and service available
        architecture_context = None
        if include_architecture and architecture_service:
            try:
                # Select architecture slice
                arch_slice = architecture_service.select_slice(
                    query=query,
                    scope=scope,
                    revision=revision,
                    tenant=tenant,
                    max_nodes=10,  # Default architecture budget
                    max_tokens=500
                )
                
                # Convert to architecture context format
                architecture_context = {
                    "schema": SCHEMA_ARCHITECTURE_PREFILL,
                    "query": query,
                    "scope": scope,
                    "revision": revision,
                    "nodes": list(arch_slice.nodes.values()),
                    "edges": arch_slice.edges,
                    "expandable_nodes": arch_slice.expandable_nodes,
                    "truncation_reason": arch_slice.truncation_reason,
                    "continuation_handle": arch_slice.continuation_handle,
                    "budget_used": arch_slice.total_budget_used
                }
                
            except Exception as e:
                # Fail gracefully - architecture is optional enhancement
                architecture_context = {
                    "schema": SCHEMA_ARCHITECTURE_PREFILL,
                    "error": str(e),
                    "fallback_to_regular_context": True
                }
        
        # Merge contexts
        result = dict(regular_context)
        if architecture_context:
            result["architecture_context"] = architecture_context
        
        # Update diagnostics
        if "diagnostics" not in result:
            result["diagnostics"] = {}
        result["diagnostics"]["architecture_included"] = architecture_context is not None
        result["diagnostics"]["include_architecture_flag"] = include_architecture
        
        return result


_codecompass_context_planner = CodeCompassContextPlanner()


def get_codecompass_context_planner() -> CodeCompassContextPlanner:
    return _codecompass_context_planner
