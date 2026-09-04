"""Scope-bound bridge to Ananta's existing retrieval port."""

from __future__ import annotations

import hashlib
import math
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from agent.services.dspy_optimization_ports import AuthorizedRetrieverPort


class CodeCompassDspyRetrieverBridge:
    def __init__(
        self,
        port: AuthorizedRetrieverPort,
        *,
        trusted_scope: Mapping[str, str],
        allowed_source_refs: Sequence[str] = (),
        max_top_k: int = 20,
        max_queries: int = 100,
        max_context_bytes: int = 256_000,
        audit_sink: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        required = {"tenant_id", "workspace_id", "repository_id", "profile_id", "role_id"}
        if set(trusted_scope) != required or any(not str(trusted_scope[key]) for key in required):
            raise ValueError("dspy_retrieval_scope_invalid")
        if not 1 <= max_top_k <= 100 or not 1 <= max_queries <= 10_000:
            raise ValueError("dspy_retrieval_limit_invalid")
        if not 1_024 <= max_context_bytes <= 5_000_000:
            raise ValueError("dspy_retrieval_context_limit_invalid")
        self._port = port
        self._scope = dict(trusted_scope)
        self._allowed_sources = frozenset(allowed_source_refs)
        self._max_top_k = max_top_k
        self._max_queries = max_queries
        self._max_context_bytes = max_context_bytes
        self._queries = 0
        self._lock = threading.Lock()
        self._audit = audit_sink or (lambda _event: None)

    def retrieve(self, query: str, *, top_k: int) -> list[dict[str, Any]]:
        if not query or len(query) > 16_384 or not 1 <= top_k <= self._max_top_k:
            raise ValueError("dspy_retrieval_request_invalid")
        with self._lock:
            if self._queries >= self._max_queries:
                raise RuntimeError("dspy_retrieval_budget_exhausted")
            self._queries += 1
            query_index = self._queries
        try:
            results = self._port.retrieve(scope=self._scope, query=query, top_k=top_k)
        except Exception:
            raise RuntimeError("dspy_retrieval_backend_unavailable") from None
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        total_bytes = 0
        for item in results:
            if set(item) - {"source_ref", "content", "score", "content_digest"}:
                raise ValueError("dspy_retrieval_result_invalid")
            source_ref = str(item.get("source_ref") or "")
            content = str(item.get("content") or "")
            digest = str(item.get("content_digest") or "")
            score = item.get("score")
            if (
                source_ref not in self._allowed_sources
                or len(content.encode()) > 64_000
                or hashlib.sha256(content.encode()).hexdigest() != digest
                or source_ref in seen
                or not isinstance(score, (int, float))
                or isinstance(score, bool)
                or not math.isfinite(float(score))
                or not 0 <= float(score) <= 1
            ):
                raise ValueError("dspy_retrieval_result_invalid")
            seen.add(source_ref)
            total_bytes += len(content.encode())
            normalized.append(dict(item))
        if len(normalized) > top_k or total_bytes > self._max_context_bytes:
            raise ValueError("dspy_retrieval_result_limit_exceeded")
        self._audit(
            {
                "schema": "ananta.dspy-retrieval-audit.v1",
                "scope_digest": hashlib.sha256(repr(sorted(self._scope.items())).encode()).hexdigest(),
                "query_digest": hashlib.sha256(query.encode()).hexdigest(),
                "query_index": query_index,
                "result_count": len(normalized),
                "context_bytes": total_bytes,
                "source_refs": sorted(seen),
            }
        )
        return normalized


__all__ = ["CodeCompassDspyRetrieverBridge"]
