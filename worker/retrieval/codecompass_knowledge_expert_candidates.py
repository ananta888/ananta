"""Expert candidate adapter over the canonical CodeCompass retrieval path."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from worker.retrieval.knowledge_expert_router import KnowledgeExpertCandidate

_RECORD_ID = re.compile(r"^expert-manifest:([0-9a-f]{64})$")


class CanonicalCodeCompassRetrievalPort(Protocol):
    def retrieve(
        self,
        payload: Mapping[str, Any],
        *,
        capability: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]: ...


class AdmittedExpertCatalogPort(Protocol):
    def resolve(self, *, manifest_digest: str) -> KnowledgeExpertCandidate | None: ...


class CodeCompassKnowledgeExpertCandidateSource:
    """Filter a dedicated record kind; never create a second search index."""

    def __init__(
        self,
        *,
        retrieval: CanonicalCodeCompassRetrievalPort,
        catalog: AdmittedExpertCatalogPort,
        capability: Mapping[str, Any] | None,
        scope: Mapping[str, str],
    ) -> None:
        self._retrieval = retrieval
        self._catalog = catalog
        self._capability = capability
        self._scope = {key: str(scope.get(key) or "") for key in ("tenant_id", "workspace_id", "repository_id")}

    def search(self, *, query: str, top_k: int) -> Sequence[KnowledgeExpertCandidate]:
        if not all(self._scope.values()):
            return ()
        result = self._retrieval.retrieve(
            {
                "schema": "codecompass.agentic-retrieval.v1",
                "kind": "request",
                "query": query,
                "mode": "hybrid",
                "task_kind": "knowledge_expert_routing",
                "scope": self._scope,
                "budget": {"top_k": max(1, min(top_k * 3, 20)), "candidate_limit": max(1, min(top_k * 6, 80))},
            },
            capability=self._capability,
        )
        if result.get("status") not in {"ok", "degraded"}:
            return ()
        candidates: list[KnowledgeExpertCandidate] = []
        for evidence in result.get("evidence") or ():
            if not isinstance(evidence, Mapping) or evidence.get("kind") != "knowledge_expert_manifest":
                continue
            match = _RECORD_ID.fullmatch(str(evidence.get("id") or ""))
            if not match:
                continue
            candidate = self._catalog.resolve(manifest_digest=match.group(1))
            if candidate is None:
                continue
            if (candidate.tenant_id, candidate.workspace_id, candidate.repository_id) != (
                self._scope["tenant_id"],
                self._scope["workspace_id"],
                self._scope["repository_id"],
            ):
                continue
            candidates.append(
                KnowledgeExpertCandidate(
                    manifest_digest=candidate.manifest_digest,
                    tenant_id=candidate.tenant_id,
                    workspace_id=candidate.workspace_id,
                    repository_id=candidate.repository_id,
                    score=float(evidence.get("score") or 0.0),
                    revoked=candidate.revoked,
                )
            )
        candidates.sort(key=lambda item: (-item.score, item.manifest_digest))
        return tuple(candidates[: max(1, min(top_k, 16))])


__all__ = [
    "AdmittedExpertCatalogPort",
    "CanonicalCodeCompassRetrievalPort",
    "CodeCompassKnowledgeExpertCandidateSource",
]
