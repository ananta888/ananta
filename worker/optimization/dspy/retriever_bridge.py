"""Scope-bound bridge to Ananta's existing retrieval port."""

from __future__ import annotations

from typing import Any, Mapping

from agent.services.dspy_optimization_ports import AuthorizedRetrieverPort


class CodeCompassDspyRetrieverBridge:
    def __init__(self, port: AuthorizedRetrieverPort, *, trusted_scope: Mapping[str, str], max_top_k: int = 20) -> None:
        required = {"tenant_id", "workspace_id", "repository_id", "profile_id"}
        if set(trusted_scope) != required or any(not str(trusted_scope[key]) for key in required):
            raise ValueError("dspy_retrieval_scope_invalid")
        if not 1 <= max_top_k <= 100:
            raise ValueError("dspy_retrieval_limit_invalid")
        self._port = port
        self._scope = dict(trusted_scope)
        self._max_top_k = max_top_k

    def retrieve(self, query: str, *, top_k: int) -> list[dict[str, Any]]:
        if not query or len(query) > 16_384 or not 1 <= top_k <= self._max_top_k:
            raise ValueError("dspy_retrieval_request_invalid")
        results = self._port.retrieve(scope=self._scope, query=query, top_k=top_k)
        normalized: list[dict[str, Any]] = []
        for item in results:
            if set(item) - {"source_ref", "content", "score", "content_digest"}:
                raise ValueError("dspy_retrieval_result_invalid")
            source_ref = str(item.get("source_ref") or "")
            content = str(item.get("content") or "")
            if not source_ref.startswith("SRC_") or len(content) > 64_000:
                raise ValueError("dspy_retrieval_result_invalid")
            normalized.append(dict(item))
        return normalized


__all__ = ["CodeCompassDspyRetrieverBridge"]
