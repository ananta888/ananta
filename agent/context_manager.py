from __future__ import annotations

import re

from agent.config import settings
from agent.repository_map_engine import ContextChunk


class ContextManager:
    """Versioned decision policy for engine routing and diversity-aware reranking."""

    def __init__(self, policy_version: str = "v1") -> None:
        self.policy_version = policy_version

    @staticmethod
    def _quota(name: str, fallback: int) -> int:
        try:
            return max(0, int(getattr(settings, name, fallback)))
        except (TypeError, ValueError):
            return fallback

    def route(self, query: str) -> dict[str, int]:
        q = query.lower()
        code_like = any(k in q for k in (
            "class", "function", "funktion", "bug", "stacktrace", "repo",
            "module", "modul", "python", ".py", "engine", "service", "tick",
            "agent", "autopilot", "methode", "klasse", "route", "controller",
            "implementier", "implement", "wie funktioniert", "wie arbeitet",
        ))
        docs_like = any(k in q for k in ("pdf", "doku", "documentation", "log", "readme", "spec"))
        fs_like = any(k in q for k in ("find", "suche", "where", "ls", "grep", "datei", "folder"))

        quotas = {"repository_map": 0, "codecompass_vector": 0, "semantic_search": 0, "agentic_search": 0}
        if code_like:
            quotas["repository_map"] += self._quota("rag_route_quota_code_repo", 12)
            quotas["codecompass_vector"] += self._quota("rag_route_quota_codecompass_vector", 6)
            quotas["semantic_search"] += self._quota("rag_route_quota_code_semantic", 2)
            quotas["agentic_search"] += 1
        if docs_like:
            quotas["semantic_search"] += self._quota("rag_route_quota_docs_semantic", 4)
            quotas["repository_map"] += self._quota("rag_route_quota_docs_repo", 2)
            quotas["codecompass_vector"] += self._quota("rag_route_quota_codecompass_vector_docs", 1)
        if fs_like:
            quotas["agentic_search"] += self._quota("rag_route_quota_fs_agentic", 3)
            quotas["repository_map"] += self._quota("rag_route_quota_fs_repo", 2)
        if all(v == 0 for v in quotas.values()):
            quotas = {
                "repository_map": self._quota("rag_route_quota_default_repo", 6),
                "codecompass_vector": self._quota("rag_route_quota_codecompass_vector_default", 4),
                "semantic_search": self._quota("rag_route_quota_default_semantic", 4),
                "agentic_search": 1,
            }
        return quotas

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return max(1, len(text) // 4)

    def rerank(
        self,
        chunks: list[ContextChunk],
        query: str,
        max_chunks: int,
        max_chars: int,
        max_tokens: int,
    ) -> list[ContextChunk]:
        tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9_]+", query) if len(t) > 2]
        engine_weights = {
            "repository_map": 1.2,
            "codecompass_vector": 0.65,
            "semantic_search": 0.85,
            "agentic_search": 0.75,
        }
        for chunk in chunks:
            text = f"{chunk.source}\n{chunk.content}".lower()
            lexical = sum(text.count(token) for token in tokens)
            weight = engine_weights.get(str(chunk.engine), 1.0)
            chunk.score = float(chunk.score) * weight + lexical * 0.25

        chunks = self._merge_same_source_chunks(chunks)
        ranked = sorted(chunks, key=lambda c: c.score, reverse=True)
        engine_heads: dict[str, ContextChunk] = {}
        for chunk in ranked:
            engine_heads.setdefault(chunk.engine, chunk)

        selected: list[ContextChunk] = []
        used = set()
        chars = 0
        token_budget = 0

        # diversity-first: keep strongest candidate per engine where possible
        for chunk in engine_heads.values():
            key = (chunk.engine, chunk.source, chunk.content[:120])
            c_tokens = self.estimate_tokens(chunk.content)
            if key in used:
                continue
            if chars + len(chunk.content) > max_chars or token_budget + c_tokens > max_tokens:
                continue
            selected.append(chunk)
            used.add(key)
            chars += len(chunk.content)
            token_budget += c_tokens
            if len(selected) >= max_chunks:
                return selected

        for chunk in ranked:
            key = (chunk.engine, chunk.source, chunk.content[:120])
            c_tokens = self.estimate_tokens(chunk.content)
            if key in used:
                continue
            if len(selected) >= max_chunks:
                break
            if chars + len(chunk.content) > max_chars or token_budget + c_tokens > max_tokens:
                continue
            selected.append(chunk)
            used.add(key)
            chars += len(chunk.content)
            token_budget += c_tokens
        return selected

    @staticmethod
    def _merge_same_source_chunks(chunks: list[ContextChunk]) -> list[ContextChunk]:
        by_source: dict[str, ContextChunk] = {}
        for chunk in chunks:
            source = str(chunk.source or "")
            if not source:
                by_source[f"__empty__:{id(chunk)}"] = chunk
                continue
            existing = by_source.get(source)
            if existing is None:
                chunk.metadata = {
                    **dict(chunk.metadata or {}),
                    "cross_engine_signals": str(chunk.engine),
                }
                by_source[source] = chunk
                continue
            existing_signals = {
                item.strip()
                for item in str((existing.metadata or {}).get("cross_engine_signals") or existing.engine).split(",")
                if item.strip()
            }
            existing_signals.add(str(chunk.engine))
            existing.metadata = {
                **dict(existing.metadata or {}),
                "cross_engine_signals": ",".join(sorted(existing_signals)),
            }
            existing.score = max(float(existing.score), float(chunk.score)) + min(float(existing.score), float(chunk.score)) * 0.15
            if len(str(chunk.content or "")) > len(str(existing.content or "")) and float(chunk.score) >= float(existing.score) * 0.8:
                existing.content = chunk.content
        return list(by_source.values())
