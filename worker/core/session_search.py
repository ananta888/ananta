"""Session search over worker traces and artifacts.

EW-T030: Search can find prior tasks, failures, patch artifacts,
          verification artifacts, and decisions.
          Result snippets are bounded and cite artifact/trace ids.
          Search respects project/tenant/scope filters.
"""
from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
from typing import Any

from ananta_contracts.memory_access import MemoryOperation, MemoryScope
from worker.core.memory_authorization import WorkerMemoryAuthorizationPort, require_worker_memory

# ── SearchTarget ──────────────────────────────────────────────────────────────

class SearchTarget(str, Enum):
    task = "task"
    failure = "failure"
    patch_artifact = "patch_artifact"
    verification_artifact = "verification_artifact"
    decision = "decision"
    trace_event = "trace_event"


# ── SearchResult ──────────────────────────────────────────────────────────────

SNIPPET_MAX_CHARS = 300


@dataclass
class SearchResult:
    target_type: SearchTarget
    id: str                    # artifact_id, task_id, or trace correlation_id
    snippet: str               # bounded to SNIPPET_MAX_CHARS
    score: float               # relevance 0.0–1.0
    project_id: str = ""
    tenant_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    matched_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_type": self.target_type.value,
            "id": self.id,
            "snippet": self.snippet[:SNIPPET_MAX_CHARS],
            "score": round(self.score, 3),
            "project_id": self.project_id,
            "tenant_id": self.tenant_id,
            "metadata": self.metadata,
        }


# ── Indexed document ──────────────────────────────────────────────────────────

@dataclass
class IndexedDocument:
    target_type: SearchTarget
    id: str
    content: str
    project_id: str = ""
    tenant_id: str = ""
    tags: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)
    indexed_at: float = field(default_factory=time.time)


# ── SessionSearchIndex ────────────────────────────────────────────────────────

class SessionSearchIndex:
    """In-memory search index for one worker session. EW-T030.

    Backed by simple term matching — no external search dependency.
    """

    def __init__(self, *, authorization: WorkerMemoryAuthorizationPort | None = None) -> None:
        self._authorization = authorization
        self._docs = []
        self._lock = RLock()
        self.last_reason_code = "memory_hub_authorization_required"

    def _access(self, operation):
        return require_worker_memory(self._authorization, MemoryScope.run, operation)

    def index(self, doc: IndexedDocument) -> None:
        try:
            access = self._access(MemoryOperation.append)
            namespace = access.namespace
            if (doc.tenant_id and doc.tenant_id != namespace.tenant_id
                    or doc.project_id and doc.project_id != namespace.workspace_id):
                raise ValueError("memory_namespace_mismatch")
            metadata = json.loads(json.dumps(doc.metadata, allow_nan=False))
            size = len(json.dumps({"id": doc.id, "content": doc.content, "metadata": metadata,
                                   "tags": sorted(doc.tags)}, allow_nan=False).encode("utf-8"))
            if size > 65536:
                raise ValueError("memory_value_quota_exceeded")
            with self._lock:
                self._docs = [item for item in self._docs if item[1] > time.time()]
                if len(self._docs) >= 256 or sum(item[3] for item in self._docs) + size > 262144:
                    raise ValueError("memory_store_quota_exceeded")
                if self._access(MemoryOperation.append) != access:
                    raise ValueError("memory_policy_changed")
                admitted = deepcopy(doc)
                admitted.tenant_id, admitted.project_id = namespace.tenant_id, namespace.workspace_id
                admitted.metadata = metadata
                self._docs.append((namespace.key(MemoryScope.run), access.expires_at, admitted, size))
                self.last_reason_code = "memory_access_allowed"
        except (TypeError, UnicodeError):
            self.last_reason_code = "memory_value_invalid"
        except ValueError as exc:
            self.last_reason_code = str(exc) if str(exc).startswith("memory_") else "memory_value_invalid"

    def index_task(
        self,
        *,
        task_id: str,
        summary: str,
        status: str,
        project_id: str = "",
        tenant_id: str = "",
    ) -> None:
        self.index(IndexedDocument(
            target_type=SearchTarget.task,
            id=task_id,
            content=f"task:{task_id} status:{status} {summary}",
            project_id=project_id,
            tenant_id=tenant_id,
            tags={"status:" + status},
        ))

    def index_failure(
        self,
        *,
        task_id: str,
        reason_code: str,
        detail: str,
        project_id: str = "",
        tenant_id: str = "",
    ) -> None:
        self.index(IndexedDocument(
            target_type=SearchTarget.failure,
            id=task_id,
            content=f"failure task:{task_id} reason:{reason_code} {detail}",
            project_id=project_id,
            tenant_id=tenant_id,
            tags={"failure", "reason:" + reason_code},
        ))

    def index_artifact(
        self,
        *,
        artifact_id: str,
        kind: str,
        summary: str,
        provenance: str,
        project_id: str = "",
        tenant_id: str = "",
    ) -> None:
        target = (
            SearchTarget.patch_artifact
            if "patch" in kind
            else SearchTarget.verification_artifact
            if "verif" in kind
            else SearchTarget.task
        )
        self.index(IndexedDocument(
            target_type=target,
            id=artifact_id,
            content=f"artifact:{artifact_id} kind:{kind} provenance:{provenance} {summary}",
            project_id=project_id,
            tenant_id=tenant_id,
            tags={"artifact", "kind:" + kind},
            metadata={"kind": kind, "provenance": provenance},
        ))

    def index_decision(
        self,
        *,
        correlation_id: str,
        decision: str,
        reason_code: str,
        operation: str = "",
        project_id: str = "",
        tenant_id: str = "",
    ) -> None:
        self.index(IndexedDocument(
            target_type=SearchTarget.decision,
            id=correlation_id,
            content=f"decision:{decision} reason:{reason_code} op:{operation}",
            project_id=project_id,
            tenant_id=tenant_id,
            tags={"decision", "decision:" + decision},
        ))

    def search(
        self,
        query: str,
        *,
        target_types: list[SearchTarget] | None = None,
        project_id: str = "",
        tenant_id: str = "",
        max_results: int = 10,
    ) -> list[SearchResult]:
        """Term-based search with scope filtering. EW-T030."""
        terms = _tokenize(query)
        if not terms:
            return []

        if type(max_results) is not int or not 1 <= max_results <= 100:
            self.last_reason_code = "memory_query_invalid"
            return []
        try:
            access = self._access(MemoryOperation.read)
        except ValueError as exc:
            self.last_reason_code = str(exc)
            return []
        namespace = access.namespace
        if (tenant_id and tenant_id != namespace.tenant_id
                or project_id and project_id != namespace.workspace_id):
            self.last_reason_code = "memory_namespace_mismatch"
            return []
        with self._lock:
            self._docs = [item for item in self._docs if item[1] > time.time()]
            documents = [deepcopy(doc) for key, _expiry, doc, _size in self._docs
                         if key == namespace.key(MemoryScope.run)]
        results: list[SearchResult] = []
        for doc in documents:
            # Scope filter
            if project_id and doc.project_id and doc.project_id != project_id:
                continue
            if tenant_id and doc.tenant_id and doc.tenant_id != tenant_id:
                continue
            if target_types and doc.target_type not in target_types:
                continue

            score = _score(terms, doc)
            if score > 0:
                snippet = _extract_snippet(query, doc.content)
                results.append(SearchResult(
                    target_type=doc.target_type,
                    id=doc.id,
                    snippet=snippet,
                    score=score,
                    project_id=doc.project_id,
                    tenant_id=doc.tenant_id,
                    metadata=doc.metadata,
                ))

        try:
            if self._access(MemoryOperation.read) != access:
                raise ValueError("memory_policy_changed")
        except ValueError as exc:
            self.last_reason_code = str(exc)
            return []
        self.last_reason_code = "memory_access_allowed"
        results.sort(key=lambda r: -r.score)
        return results[:max_results]

    def clear(self) -> None:
        with self._lock:
            self._docs.clear()


# ── Scoring and snippet helpers ───────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in re.split(r"[\s:,;/]+", text.strip()) if len(t) > 1]


def _score(terms: list[str], doc: IndexedDocument) -> float:
    content_lower = doc.content.lower()
    tag_lower = {t.lower() for t in doc.tags}
    hits = 0
    for term in terms:
        if term in content_lower:
            hits += 1
        if any(term in tag for tag in tag_lower):
            hits += 0.5   # tag matches get a boost
    return round(min(1.0, hits / max(len(terms), 1)), 3)


def _extract_snippet(query: str, content: str, max_chars: int = SNIPPET_MAX_CHARS) -> str:
    """Extract a bounded snippet centered on the first query term match."""
    terms = _tokenize(query)
    content_lower = content.lower()
    best_pos = len(content)
    for term in terms:
        pos = content_lower.find(term)
        if 0 <= pos < best_pos:
            best_pos = pos

    if best_pos == len(content):
        return content[:max_chars]

    start = max(0, best_pos - max_chars // 3)
    end = min(len(content), start + max_chars)
    snippet = content[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(content):
        snippet = snippet + "…"
    return snippet
