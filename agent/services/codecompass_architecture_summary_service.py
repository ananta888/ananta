"""Deterministic architecture summaries with revision-bound cache."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

_CACHE: dict[str, dict[str, Any]] = {}


def summary_cache_key(
    *,
    revision: str,
    node_id: str,
    evidence_hash: str,
    prompt_version: str = "det-v1",
) -> str:
    raw = "|".join([revision, node_id, evidence_hash, prompt_version])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def evidence_hash(source_refs: list[str], excerpt: str = "") -> str:
    payload = "\n".join([*sorted(source_refs), excerpt])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


class CodeCompassArchitectureSummaryService:
    def __init__(self) -> None:
        self._cache = _CACHE

    def summarize(self, node: Mapping[str, Any], *, revision: str) -> dict[str, Any]:
        refs = [str(item) for item in list(node.get("source_refs") or []) if str(item)]
        excerpt = str(node.get("short_summary") or "")
        digest = evidence_hash(refs, excerpt)
        key = summary_cache_key(revision=revision, node_id=str(node.get("id") or ""), evidence_hash=digest)
        cached = self._cache.get(key)
        if cached is not None:
            return dict(cached)
        if not refs and not excerpt:
            result = {
                "summary": "",
                "status": "summary_unavailable",
                "derived": False,
                "source_refs": [],
                "cache_key": key,
            }
            self._cache[key] = result
            return dict(result)
        title = str(node.get("title") or node.get("id") or "node")
        level = str(node.get("level") or "unknown")
        path = str(node.get("path") or (refs[0] if refs else ""))
        text = f"{title} is a {level}"
        if path:
            text += f" grounded in {path}"
        if excerpt:
            text += f": {excerpt[:160]}"
        result = {
            "summary": text[:280],
            "status": "ok",
            "derived": True,
            "source_refs": refs or [path or str(node.get("id") or "")],
            "cache_key": key,
        }
        self._cache[key] = result
        return dict(result)

    def invalidate(self, cache_key: str) -> None:
        self._cache.pop(str(cache_key), None)
