from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_CONTEXT_PACKAGE = "codecompass_context_package.v1"
SCHEMA_SEARCH_RESULT = "codecompass_search_result.v1"
SCHEMA_FILE_CONTEXT_RESULT = "codecompass_file_context_result.v1"
SCHEMA_GRAPH_EXPANSION = "codecompass_graph_expansion.v1"
SCHEMA_DOMAIN_MAP = "codecompass_domain_map.v1"

_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?([^\s'\";]+)"),
    re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/=-]{16,}"),
]


@dataclass(frozen=True)
class CodeCompassContextToolConfig:
    enabled: bool = True
    default_mode: str = "balanced"
    max_files: int = 20
    max_original_files: int = 8
    max_total_bytes: int = 262_144
    max_tokens_compact: int = 4_096
    max_tokens_balanced: int = 12_000
    max_tokens_deep: int = 32_000
    external_cloud_policy: str = "metadata_only_unless_allowed"
    allow_raw_jsonl_records: bool = False
    require_reason_for_file_context: bool = True
    domain_scope_required_for_large_context: bool = True

    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None = None) -> "CodeCompassContextToolConfig":
        data = dict(raw or {})

        def _get(name: str, default: Any) -> Any:
            return data.get(f"codecompass_context_tools.{name}", data.get(name, default))

        def _int(name: str, default: int, lo: int, hi: int) -> int:
            try:
                return max(lo, min(int(_get(name, default)), hi))
            except (TypeError, ValueError):
                return default

        def _bool(name: str, default: bool) -> bool:
            value = _get(name, default)
            if isinstance(value, bool):
                return value
            token = str(value).strip().lower()
            if token in {"1", "true", "yes", "on", "an", "ja"}:
                return True
            if token in {"0", "false", "no", "off", "aus", "nein"}:
                return False
            return default

        mode = str(_get("default_mode", cls.default_mode)).strip().lower() or cls.default_mode
        if mode not in {"compact", "balanced", "deep"}:
            mode = cls.default_mode
        return cls(
            enabled=_bool("enabled", cls.enabled),
            default_mode=mode,
            max_files=_int("max_files", cls.max_files, 1, 200),
            max_original_files=_int("max_original_files", cls.max_original_files, 0, 50),
            max_total_bytes=_int("max_total_bytes", cls.max_total_bytes, 1_024, 5_000_000),
            max_tokens_compact=_int("max_tokens_compact", cls.max_tokens_compact, 512, 200_000),
            max_tokens_balanced=_int("max_tokens_balanced", cls.max_tokens_balanced, 512, 200_000),
            max_tokens_deep=_int("max_tokens_deep", cls.max_tokens_deep, 512, 500_000),
            external_cloud_policy=str(_get("external_cloud_policy", cls.external_cloud_policy)).strip()
            or cls.external_cloud_policy,
            allow_raw_jsonl_records=_bool("allow_raw_jsonl_records", cls.allow_raw_jsonl_records),
            require_reason_for_file_context=_bool(
                "require_reason_for_file_context", cls.require_reason_for_file_context
            ),
            domain_scope_required_for_large_context=_bool(
                "domain_scope_required_for_large_context", cls.domain_scope_required_for_large_context
            ),
        )

    def token_budget_for_mode(self, mode: str | None) -> int:
        resolved = str(mode or self.default_mode).strip().lower()
        if resolved == "compact":
            return self.max_tokens_compact
        if resolved == "deep":
            return self.max_tokens_deep
        return self.max_tokens_balanced


class CodeCompassContextService:
    """Hub-side facade for policy-bounded CodeCompass context tools."""

    def __init__(self, *, config: CodeCompassContextToolConfig | None = None) -> None:
        self._config = config or CodeCompassContextToolConfig()

    def resolve_context(
        self,
        *,
        query: str,
        task_kind: str | None = None,
        mode: str | None = None,
        working_files: list[str] | None = None,
        domain_hint: str | None = None,
        domain_scope: str | None = None,
        max_tokens: int | None = None,
        max_files: int | None = None,
        include_original_files: bool = False,
        include_jsonl_records: bool = False,
        include_graph: bool = False,
        llm_scope: str | None = None,
        workspace_dir: str | None = None,
    ) -> dict[str, Any]:
        q = str(query or "").strip()
        if not q:
            return self._error_package(query=q, reason_code="query_required")
        effective_mode = str(mode or self._config.default_mode).strip().lower() or self._config.default_mode
        token_budget = self._bounded_int(max_tokens, self._config.token_budget_for_mode(effective_mode), 512, 500_000)
        file_budget = self._bounded_int(max_files, self._config.max_files, 1, self._config.max_files)
        warnings: list[str] = []
        denied_items: list[dict[str, Any]] = []
        selection_trace: list[dict[str, Any]] = []

        if (
            effective_mode == "deep"
            and self._config.domain_scope_required_for_large_context
            and not domain_scope
            and not working_files
        ):
            effective_mode = "balanced"
            warnings.append("deep_context_downgraded_requires_domain_scope_or_working_files")

        llm_scope_value = str(llm_scope or "local").strip().lower()
        if llm_scope_value.startswith("external") and self._config.external_cloud_policy == "metadata_only_unless_allowed":
            if include_original_files:
                denied_items.append({
                    "kind": "context_files",
                    "reason_code": "external_cloud_metadata_only",
                    "requested": "include_original_files",
                })
            include_original_files = False
            include_jsonl_records = False

        candidates = self._candidate_files(
            query=q,
            working_files=working_files or [],
            domain_hint=domain_hint,
            limit=file_budget,
            selection_trace=selection_trace,
        )

        context_files: list[dict[str, Any]] = []
        if include_original_files:
            requested_paths = [str(row.get("path") or "") for row in candidates if row.get("requires_read")]
            file_result = self.get_file_context(
                paths=requested_paths[: self._config.max_original_files],
                max_total_bytes=self._config.max_total_bytes,
                reason="resolve_context_include_original_files",
                workspace_dir=workspace_dir,
            )
            context_files = list(file_result.get("context_files") or [])
            denied_items.extend(list(file_result.get("denied_items") or []))
            warnings.extend(list(file_result.get("warnings") or []))

        graph_edges: list[dict[str, Any]] = []
        if include_graph:
            graph = self.expand_graph(
                seeds=[str(row.get("node_id") or row.get("path") or "") for row in candidates[:5]],
                max_nodes=40,
                domain_scope=domain_scope,
            )
            graph_edges = list(graph.get("graph_edges") or [])
            warnings.extend(list(graph.get("warnings") or []))

        jsonl_records = []
        if include_jsonl_records and self._config.allow_raw_jsonl_records:
            jsonl_records = [
                {
                    "record_id": row.get("candidate_id"),
                    "path": row.get("path"),
                    "source_output_kinds": row.get("source_output_kinds", []),
                    "provenance": row.get("provenance", {}),
                }
                for row in candidates[:file_budget]
            ]
        elif include_jsonl_records:
            warnings.append("raw_jsonl_records_disabled_by_policy")

        domain_map = self.get_domain_map(
            domain_hint=domain_hint or domain_scope,
            include_files=True,
            include_edges=include_graph,
            max_entries=min(20, file_budget),
        )

        payload_core = {
            "query": q,
            "task_kind": str(task_kind or ""),
            "mode": effective_mode,
            "candidate_files": candidates,
            "context_files": context_files,
            "jsonl_records": jsonl_records,
            "symbols": self._symbols_from_candidates(candidates),
            "graph_edges": graph_edges,
            "domain_map": domain_map.get("domain_map", {}),
            "denied_items": denied_items,
            "warnings": sorted(set(str(w) for w in warnings)),
        }
        context_hash = self._stable_hash(payload_core)
        return {
            "schema": SCHEMA_CONTEXT_PACKAGE,
            "package_id": f"ccpkg:{context_hash[:16]}",
            "query": q,
            "task_kind": str(task_kind or ""),
            "mode": effective_mode,
            "created_at": time.time(),
            "manifest_hash": self._manifest_hash(),
            "context_hash": context_hash,
            "budget": {
                "max_tokens": token_budget,
                "max_files": file_budget,
                "max_original_files": self._config.max_original_files,
                "max_total_bytes": self._config.max_total_bytes,
            },
            "policy": {
                "llm_scope": llm_scope_value,
                "external_cloud_policy": self._config.external_cloud_policy,
                "allow_raw_jsonl_records": self._config.allow_raw_jsonl_records,
                "domain_scope_required_for_large_context": self._config.domain_scope_required_for_large_context,
            },
            "candidate_files": candidates,
            "context_files": context_files,
            "jsonl_records": jsonl_records,
            "symbols": payload_core["symbols"],
            "graph_edges": graph_edges,
            "domain_map": payload_core["domain_map"],
            "retrieval_trace": {
                "source": "codecompass_context_service",
                "candidate_count": len(candidates),
                "domain_hint": str(domain_hint or ""),
                "domain_scope": str(domain_scope or ""),
            },
            "selection_trace": selection_trace,
            "why_this_context": [
                f"{row.get('path')}: {row.get('reason')}" for row in candidates[: min(5, len(candidates))]
            ],
            "denied_items": denied_items,
            "warnings": payload_core["warnings"],
        }

    def search_symbols(
        self,
        *,
        query: str,
        record_kinds: list[str] | None = None,
        path_globs: list[str] | None = None,
        domain_hint: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        q = str(query or "").strip()
        if not q:
            return {"schema": SCHEMA_SEARCH_RESULT, "status": "error", "error": "query_required", "records": []}
        effective_limit = self._bounded_int(limit, 20, 1, 100)
        records = []
        warnings: list[str] = []
        try:
            from agent.services.rag_helper_index_service import get_rag_helper_index_service

            hits = get_rag_helper_index_service().retrieve(profile=None, query=q, limit=effective_limit)
        except Exception as exc:
            hits = []
            warnings.append(f"codecompass_search_unavailable:{exc}")
        allowed_kinds = {str(item).strip() for item in list(record_kinds or []) if str(item).strip()}
        glob_tokens = [str(item).replace("*", "").strip() for item in list(path_globs or []) if str(item).strip()]
        for idx, hit in enumerate(list(hits or [])[:effective_limit]):
            if not isinstance(hit, dict):
                continue
            meta = dict(hit.get("metadata") or {})
            path = str(hit.get("path") or hit.get("source") or meta.get("path") or meta.get("file") or "")
            kind = str(meta.get("kind") or hit.get("kind") or "retrieval_chunk")
            if allowed_kinds and kind not in allowed_kinds:
                continue
            if glob_tokens and not any(token in path for token in glob_tokens):
                continue
            records.append({
                "record_id": str(hit.get("id") or f"search:{idx}"),
                "record_kind": kind,
                "path": path,
                "symbol": str(hit.get("symbol") or meta.get("symbol") or meta.get("name") or ""),
                "score": self._to_float(hit.get("score")) or 0.0,
                "excerpt": str(hit.get("content") or hit.get("text") or hit.get("snippet") or "")[:1500],
                "domain_hint": str(domain_hint or ""),
                "provenance": self._provenance(source="codecompass.search", path=path, score=hit.get("score")),
            })
        return {
            "schema": SCHEMA_SEARCH_RESULT,
            "status": "ok" if records else "degraded",
            "query": q,
            "records": records,
            "record_count": len(records),
            "warnings": warnings if warnings else ([] if records else ["no_results"]),
        }

    def expand_graph(
        self,
        *,
        seeds: list[str],
        max_depth: int | None = 1,
        max_nodes: int | None = 40,
        relation_types: list[str] | None = None,
        domain_scope: str | None = None,
    ) -> dict[str, Any]:
        effective_nodes = self._bounded_int(max_nodes, 40, 1, 200)
        seed_values = [str(seed).strip() for seed in list(seeds or []) if str(seed).strip()]
        if not seed_values:
            return {
                "schema": SCHEMA_GRAPH_EXPANSION,
                "status": "error",
                "error": "seeds_required",
                "nodes": [],
                "graph_edges": [],
            }
        try:
            from agent.services.tools.codecompass_tools import _resolve_graph_store
            from worker.retrieval.codecompass_graph_expansion import expand_codecompass_graph

            store, index_id = _resolve_graph_store({})
            if store is None:
                raise RuntimeError("no_completed_graph_index")
            expansion = expand_codecompass_graph(store=store, seed_node_ids=seed_values, profile="bugfix_local")
            nodes = list(expansion.get("nodes") or [])[:effective_nodes]
            graph_edges = [
                {
                    "source": str(edge.get("source") or edge.get("from") or ""),
                    "target": str(edge.get("target") or edge.get("to") or ""),
                    "relation_type": str(edge.get("type") or edge.get("relation_type") or ""),
                    "weight": self._to_float(edge.get("weight")) or 1.0,
                    "reason": "codecompass_graph_expansion",
                    "provenance": self._provenance(source="codecompass.expand_graph", path="", score=None),
                }
                for edge in list(expansion.get("edges") or [])[:effective_nodes]
                if isinstance(edge, dict)
            ]
            return {
                "schema": SCHEMA_GRAPH_EXPANSION,
                "status": "ok",
                "knowledge_index_id": index_id,
                "seeds": seed_values,
                "max_depth": 0 if max_depth == 0 else self._bounded_int(max_depth, 1, 1, 8),
                "domain_scope": str(domain_scope or ""),
                "relation_types": [str(item) for item in list(relation_types or [])],
                "nodes": nodes,
                "graph_edges": graph_edges,
                "warnings": list(expansion.get("warnings") or []),
            }
        except Exception as exc:
            return {
                "schema": SCHEMA_GRAPH_EXPANSION,
                "status": "degraded",
                "seeds": seed_values,
                "nodes": [],
                "graph_edges": [],
                "warnings": [f"codecompass_graph_unavailable:{exc}"],
            }

    def get_file_context(
        self,
        *,
        paths: list[str],
        line_ranges: list[dict[str, Any]] | None = None,
        max_bytes_per_file: int | None = None,
        max_total_bytes: int | None = None,
        redaction_mode: str = "auto",
        reason: str | None = None,
        workspace_dir: str | None = None,
    ) -> dict[str, Any]:
        if self._config.require_reason_for_file_context and not str(reason or "").strip():
            return {
                "schema": SCHEMA_FILE_CONTEXT_RESULT,
                "status": "error",
                "error": "reason_required",
                "context_files": [],
                "denied_items": [],
            }
        root = Path(workspace_dir or ".").resolve()
        per_file_limit = self._bounded_int(max_bytes_per_file, 32_768, 128, 1_000_000)
        total_limit = self._bounded_int(max_total_bytes, self._config.max_total_bytes, 128, self._config.max_total_bytes)
        range_map = self._line_range_map(line_ranges or [])
        context_files: list[dict[str, Any]] = []
        denied_items: list[dict[str, Any]] = []
        warnings: list[str] = []
        consumed = 0
        for path in [str(item or "").strip() for item in list(paths or []) if str(item or "").strip()]:
            if len(context_files) >= self._config.max_original_files:
                denied_items.append({"path": path, "reason_code": "max_original_files_exceeded"})
                continue
            resolved, deny = self._resolve_workspace_path(root=root, path=path)
            if deny:
                denied_items.append({"path": path, "reason_code": deny})
                continue
            try:
                raw_bytes = resolved.read_bytes()
            except OSError as exc:
                denied_items.append({"path": path, "reason_code": f"read_failed:{exc}"})
                continue
            if b"\x00" in raw_bytes[:4096]:
                denied_items.append({"path": path, "reason_code": "binary_file_denied"})
                continue
            if consumed >= total_limit:
                denied_items.append({"path": path, "reason_code": "max_total_bytes_exceeded"})
                continue
            text = raw_bytes.decode("utf-8", errors="replace")
            line_start, line_end = range_map.get(path, (None, None))
            if line_start is not None or line_end is not None:
                lines = text.splitlines()
                start = max(1, int(line_start or 1))
                end = min(len(lines), int(line_end or len(lines)))
                text = "\n".join(lines[start - 1:end])
            encoded = text.encode("utf-8", errors="replace")
            remaining = max(0, total_limit - consumed)
            limit = min(per_file_limit, remaining)
            truncated = len(encoded) > limit
            if truncated:
                text = encoded[:limit].decode("utf-8", errors="replace")
                warnings.append("file_context_truncated")
            redacted, redaction_status = self._redact(text, enabled=redaction_mode != "none")
            consumed += len(redacted.encode("utf-8", errors="replace"))
            context_files.append({
                "path": path,
                "content": redacted,
                "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                "byte_count": len(raw_bytes),
                "line_count": len(raw_bytes.decode("utf-8", errors="replace").splitlines()),
                "line_ranges": [{"line_start": line_start, "line_end": line_end}] if line_start or line_end else [],
                "redaction_status": redaction_status,
                "truncated": truncated,
                "read_at": time.time(),
                "provenance": self._provenance(source="codecompass.get_file_context", path=path, score=None),
            })
        return {
            "schema": SCHEMA_FILE_CONTEXT_RESULT,
            "status": "ok" if context_files else "degraded",
            "context_files": context_files,
            "denied_items": denied_items,
            "warnings": sorted(set(warnings)),
            "budget": {"max_bytes_per_file": per_file_limit, "max_total_bytes": total_limit, "bytes_used": consumed},
        }

    def get_domain_map(
        self,
        *,
        domain_hint: str | None = None,
        include_files: bool = True,
        include_edges: bool = False,
        max_entries: int = 20,
    ) -> dict[str, Any]:
        hint = str(domain_hint or "").strip()
        query = hint or "architecture entry points"
        search = self.search_symbols(query=query, domain_hint=hint, limit=max_entries)
        files = []
        if include_files:
            seen: set[str] = set()
            for record in list(search.get("records") or []):
                path = str(record.get("path") or "")
                if not path or path in seen:
                    continue
                seen.add(path)
                files.append({
                    "path": path,
                    "score": record.get("score", 0.0),
                    "reason": "domain_hint_match" if hint else "architecture_search",
                })
        edges = []
        if include_edges and files:
            graph = self.expand_graph(seeds=[row["path"] for row in files[:5]], max_nodes=max_entries, domain_scope=hint)
            edges = list(graph.get("graph_edges") or [])
        return {
            "schema": SCHEMA_DOMAIN_MAP,
            "status": "ok" if files or edges else "degraded",
            "domain_map": {
                "domain_hint": hint,
                "entry_points": files[:5],
                "key_files": files,
                "test_files": [row for row in files if "/test" in row["path"] or row["path"].startswith("tests/")],
                "config_files": [row for row in files if "config" in row["path"].lower()],
                "known_boundaries": [],
                "edges": edges,
            },
            "warnings": [] if files or edges else ["domain_map_empty"],
        }

    def _candidate_files(
        self,
        *,
        query: str,
        working_files: list[str],
        domain_hint: str | None,
        limit: int,
        selection_trace: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for path in working_files:
            p = str(path or "").strip()
            if not p or p in seen:
                continue
            seen.add(p)
            rows.append(self._candidate(path=p, score=1.0, reason="explicit_working_file", source_kinds=["user_input"]))
            selection_trace.append({"path": p, "decision": "selected", "reason": "explicit_working_file"})
        search = self.search_symbols(query=query, domain_hint=domain_hint, limit=max(limit * 2, limit))
        for record in list(search.get("records") or []):
            path = str(record.get("path") or "")
            if not path or path in seen:
                continue
            seen.add(path)
            rows.append(
                self._candidate(
                    path=path,
                    score=float(record.get("score") or 0.0),
                    reason="symbol_or_text_match",
                    source_kinds=[str(record.get("record_kind") or "retrieval_chunk")],
                    symbol=str(record.get("symbol") or "") or None,
                    record_id=str(record.get("record_id") or "") or None,
                )
            )
            selection_trace.append({"path": path, "decision": "selected", "reason": "symbol_or_text_match"})
            if len(rows) >= limit:
                break
        return rows[:limit]

    def _candidate(
        self,
        *,
        path: str,
        score: float,
        reason: str,
        source_kinds: list[str],
        symbol: str | None = None,
        record_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "path": path,
            "score": score,
            "reason": reason,
            "source_output_kinds": source_kinds,
            "source_record_ids": [record_id] if record_id else [],
            "matched_symbols": [symbol] if symbol else [],
            "relation_path": [],
            "requires_read": True,
            "sensitivity": "unknown",
            "read_policy": "policy_checked_before_materialization",
            "provenance": self._provenance(source="codecompass.resolve_context", path=path, score=score),
        }
        payload["candidate_id"] = f"cfile:{self._stable_hash(payload)[:16]}"
        return payload

    def _symbols_from_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for row in candidates:
            for symbol in list(row.get("matched_symbols") or []):
                out.append({"symbol": symbol, "path": row.get("path"), "candidate_id": row.get("candidate_id")})
        return out

    def _error_package(self, *, query: str, reason_code: str) -> dict[str, Any]:
        return {
            "schema": SCHEMA_CONTEXT_PACKAGE,
            "package_id": "ccpkg:error",
            "query": query,
            "status": "error",
            "reason_code": reason_code,
            "candidate_files": [],
            "context_files": [],
            "denied_items": [],
            "warnings": [reason_code],
        }

    def _resolve_workspace_path(self, *, root: Path, path: str) -> tuple[Path, str | None]:
        candidate = Path(path)
        if candidate.is_absolute():
            return candidate, "absolute_path_denied"
        resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            return resolved, "path_outside_workspace"
        if not resolved.exists() or not resolved.is_file():
            return resolved, "file_not_found"
        return resolved, None

    def _line_range_map(self, ranges: list[dict[str, Any]]) -> dict[str, tuple[int | None, int | None]]:
        out: dict[str, tuple[int | None, int | None]] = {}
        for row in ranges:
            if not isinstance(row, dict):
                continue
            path = str(row.get("path") or "").strip()
            if not path:
                continue
            out[path] = (self._to_int(row.get("line_start")), self._to_int(row.get("line_end")))
        return out

    def _redact(self, text: str, *, enabled: bool) -> tuple[str, str]:
        if not enabled:
            return text, "disabled"
        value = text
        redacted = False
        for pattern in _SECRET_PATTERNS:
            new_value = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", value)
            redacted = redacted or new_value != value
            value = new_value
        return value, "redacted" if redacted else "clean"

    def _manifest_hash(self) -> str:
        try:
            from agent.config import settings

            root = Path(getattr(settings, "rag_repo_root", ".")).resolve()
            marker = str(root)
        except Exception:
            marker = "."
        return hashlib.sha256(marker.encode("utf-8")).hexdigest()

    def _provenance(self, *, source: str, path: str, score: Any) -> dict[str, Any]:
        return {
            "source": source,
            "manifest_hash": self._manifest_hash(),
            "record_type": "codecompass_context",
            "score": self._to_float(score),
            "path": path,
            "policy_decision": "pending_file_policy" if path else "metadata_only",
            "redaction_status": "not_applicable",
        }

    def _stable_hash(self, payload: Any) -> str:
        raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _bounded_int(self, value: Any, default: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(int(value), hi))
        except (TypeError, ValueError):
            return default

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


_codecompass_context_service = CodeCompassContextService()


def get_codecompass_context_service() -> CodeCompassContextService:
    return _codecompass_context_service
