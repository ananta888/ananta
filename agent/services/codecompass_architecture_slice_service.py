"""Select a query-relevant hierarchical architecture slice."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from agent.services.codecompass_architecture_budget import (
    apply_architecture_budget,
    resolve_architecture_budget,
)
from agent.services.codecompass_architecture_summary_service import (
    CodeCompassArchitectureSummaryService,
)
from ananta_contracts.codecompass_hierarchical_architecture import (
    LEVELS,
    project_hierarchy,
)

SCHEMA_ID = "codecompass.hierarchical-architecture-context.v1"

# Stable entry points that explain the subsystem itself.  Broad queries such
# as "what is CodeCompass" should not be decided by filename ordering among
# hundreds of equally matching implementation and migration files.
_CODECOMPASS_OVERVIEW_ENTRYPOINTS = (
    "agent/services/tools/codecompass_",
    "agent/services/codecompass_architecture_",
    "agent/services/codecompass_agentic_retrieval",
    "agent/services/codecompass_context_planner",
    "worker/retrieval/codecompass_",
    "ananta_codecompass/architecture_intelligence",
)


def encode_handle(*, revision: str, node_id: str) -> str:
    payload = json.dumps({"r": revision, "n": node_id}, separators=(",", ":"), sort_keys=True)
    return "hac:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12] + ":" + node_id


def decode_handle(handle: str, *, revision: str) -> str:
    raw = str(handle or "")
    if not raw.startswith("hac:"):
        raise ValueError("invalid_architecture_handle")
    parts = raw.split(":", 2)
    if len(parts) != 3 or not parts[2]:
        raise ValueError("invalid_architecture_handle")
    expected = encode_handle(revision=revision, node_id=parts[2])
    if expected != raw:
        raise ValueError("stale_architecture_handle")
    return parts[2]


class CodeCompassArchitectureSliceService:
    def __init__(self, *, summary_service: CodeCompassArchitectureSummaryService | None = None) -> None:
        self._summaries = summary_service or CodeCompassArchitectureSummaryService()

    def build_slice(
        self,
        *,
        query: str,
        records: list[Mapping[str, Any]],
        edges: list[Mapping[str, Any]] | None = None,
        capability: Mapping[str, Any] | None = None,
        profile: str = "overview",
        focus_node_id: str | None = None,
        parent_max_tokens: int | None = None,
        include_prefill: bool = True,
    ) -> dict[str, Any]:
        capability = dict(capability or {})
        revision = str(capability.get("revision") or "")
        if capability and (not revision or not capability.get("workspace_id")):
            raise ValueError("empty_scope")
        if capability.get("allowed_paths"):
            allowed = [str(item).replace("\\", "/").strip("/") for item in capability.get("allowed_paths") or []]
            records = [
                record
                for record in records
                if _path_allowed(str(record.get("path") or record.get("file") or record.get("id") or ""), allowed)
            ]
        projected = project_hierarchy(records=records, edges=edges, revision=revision)
        ranked = self._rank_nodes(projected["nodes"], query=query, focus_node_id=focus_node_id)
        ranked = self._include_ancestor_context(
            ranked,
            nodes=projected["nodes"],
            edges=projected["edges"],
        )
        budget = resolve_architecture_budget(profile=profile, parent_max_tokens=parent_max_tokens)
        applied = apply_architecture_budget(nodes=ranked, edges=projected["edges"], budget=budget)
        nodes = []
        for node in applied["nodes"]:
            summary = self._summaries.summarize(node, revision=revision)
            item = dict(node)
            item["short_summary"] = summary["summary"] or "summary_unavailable"
            if summary["status"] == "summary_unavailable":
                item["responsibilities"] = []
            elif not item.get("responsibilities"):
                item["responsibilities"] = [summary["summary"]]
            item["handle"] = encode_handle(revision=revision, node_id=str(item["id"]))
            item["source_refs"] = list(summary["source_refs"] or item.get("source_refs") or [item["id"]])
            nodes.append(item)
        handles = [item["handle"] for item in nodes if item.get("expandable")]
        return {
            "schema": SCHEMA_ID,
            "query": query,
            "root_scope": {
                "tenant_id": str(capability.get("tenant_id") or ""),
                "workspace_id": str(capability.get("workspace_id") or ""),
                "revision": revision,
                "source_scope": str(capability.get("source_scope") or ""),
            },
            "levels": list(LEVELS),
            "nodes": nodes,
            "edges": applied["edges"],
            "summaries": [item["short_summary"] for item in nodes if item.get("short_summary")],
            "evidence": [
                {"path": item.get("path") or "", "source_refs": item.get("source_refs") or []}
                for item in nodes
            ],
            "budgets": applied["budget"],
            "truncated": bool(applied["truncated"]),
            "truncation_reason": applied["truncation_reason"] or "",
            "expansion_handles": handles,
            "warnings": ["unknown_nodes_present"] if projected["unknown_count"] else [],
            "prefill_included": bool(include_prefill),
        }

    def navigate(
        self,
        slice_payload: Mapping[str, Any],
        *,
        action: str,
        handle: str,
        records: list[Mapping[str, Any]],
        edges: list[Mapping[str, Any]] | None = None,
        capability: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        revision = str((capability or {}).get("revision") or slice_payload.get("root_scope", {}).get("revision") or "")
        node_id = decode_handle(handle, revision=revision)
        profile = {
            "expand": "component",
            "parents": "overview",
            "siblings": "subsystem",
            "dependencies": "component",
            "overview": "overview",
        }.get(action, "overview")
        return self.build_slice(
            query=str(slice_payload.get("query") or node_id),
            records=records,
            edges=edges,
            capability=capability or slice_payload.get("root_scope"),
            profile=profile,
            focus_node_id=None if action == "overview" else node_id,
        )

    def _rank_nodes(
        self,
        nodes: list[dict[str, Any]],
        *,
        query: str,
        focus_node_id: str | None,
    ) -> list[dict[str, Any]]:
        tokens = {part.lower() for part in str(query or "").replace("/", " ").split() if len(part) > 2}
        scored: list[tuple[float, dict[str, Any]]] = []
        for node in nodes:
            hay = " ".join(
                [
                    str(node.get("title") or ""),
                    str(node.get("path") or ""),
                    str(node.get("short_summary") or ""),
                    str(node.get("id") or ""),
                ]
            ).lower()
            overlap = sum(1 for token in tokens if token in hay)
            level_bonus = {"system": 5, "subsystem": 4, "component": 3, "file": 1, "symbol": 0.5, "unknown": 0}.get(
                str(node.get("level")),
                0,
            )
            focus = 8.0 if focus_node_id and node.get("id") == focus_node_id else 0.0
            parent_bonus = 3.0 if focus_node_id and node.get("id") and node.get("parent_id") == focus_node_id else 0.0
            if focus_node_id and node.get("id") == focus_node_id:
                parent_bonus += 3.0
            relevance = overlap * 4 + focus + parent_bonus
            if relevance <= 0:
                continue
            path = str(node.get("path") or "").replace("\\", "/").lower()
            if path.startswith(("artifacts/", "tests/", "docs/", "reference_sources/")):
                relevance *= 0.2
            elif path.startswith(("agent/", "worker/", "frontend-angular/", "rag-helper/")):
                relevance *= 1.5
            if path.startswith(
                (
                    "agent/services/codecompass",
                    "agent/services/knowledge_index",
                    "agent/routes/snakes",
                    "worker/retrieval/codecompass",
                    "ananta_codecompass/",
                )
            ):
                relevance *= 2.0
            if "codecompass" in tokens and path.startswith(
                _CODECOMPASS_OVERVIEW_ENTRYPOINTS
            ):
                relevance += 18.0
            scored.append((relevance + level_bonus, node))
        scored.sort(key=lambda item: (-item[0], item[1].get("id")))
        if scored:
            return [item[1] for item in scored]
        # A query without any graph token still receives a bounded structural
        # overview instead of an arbitrary empty result.
        return sorted(
            nodes,
            key=lambda node: (
                -{"system": 5, "subsystem": 4, "component": 3, "file": 1}.get(
                    str(node.get("level")), 0
                ),
                str(node.get("id") or ""),
            ),
        )

    @staticmethod
    def _include_ancestor_context(
        ranked: list[dict[str, Any]],
        *,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Prepend real containment ancestors for the strongest matches."""

        by_id = {str(node.get("id") or ""): node for node in nodes}
        parent_by_child = {
            str(edge.get("target") or ""): str(edge.get("source") or "")
            for edge in edges
            if edge.get("relation") == "contains"
        }
        ancestor_ids: set[str] = set()
        for seed in ranked[:8]:
            current = str(seed.get("id") or "")
            visited: set[str] = set()
            while current in parent_by_child and current not in visited:
                visited.add(current)
                current = parent_by_child[current]
                if current in by_id:
                    ancestor_ids.add(current)
        level_order = {"system": 0, "subsystem": 1, "component": 2, "file": 3, "symbol": 4}
        ancestors = sorted(
            (by_id[node_id] for node_id in ancestor_ids),
            key=lambda node: (
                level_order.get(str(node.get("level")), 9),
                len(str(node.get("path") or "").split("/")),
                str(node.get("id") or ""),
            ),
        )
        seen = {str(node.get("id") or "") for node in ancestors}
        return [
            *ancestors,
            *(node for node in ranked if str(node.get("id") or "") not in seen),
        ]


def _path_allowed(path: str, allowed: list[str]) -> bool:
    candidate = str(path or "").replace("\\", "/").strip("/")
    if not candidate:
        return False
    parts = candidate.split("/")
    for prefix in allowed:
        clean = str(prefix or "").strip("/")
        if clean and parts[: len(clean.split("/"))] == clean.split("/"):
            return True
        if candidate == clean or candidate.startswith(clean):
            return True
    return False


_slice_service = CodeCompassArchitectureSliceService()


def get_codecompass_architecture_slice_service() -> CodeCompassArchitectureSliceService:
    return _slice_service
