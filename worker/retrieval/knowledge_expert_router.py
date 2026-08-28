"""Bounded expert selection over Hub-admitted candidates."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from ananta_contracts.knowledge_expert_runtime import KnowledgeExpertRoutingDecision

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MODES = frozenset({"auto", "expert_only", "expert_plus_rag", "off"})


@dataclass(frozen=True, slots=True)
class KnowledgeExpertCandidate:
    manifest_digest: str
    tenant_id: str
    workspace_id: str
    repository_id: str
    score: float
    revoked: bool = False


class KnowledgeExpertCandidatePort(Protocol):
    def search(self, *, query: str, top_k: int) -> Sequence[KnowledgeExpertCandidate]: ...


class UncertaintyAwareKnowledgeExpertRouter:
    def __init__(
        self,
        *,
        candidates: KnowledgeExpertCandidatePort,
        threshold: float,
        top_k: int = 3,
        hysteresis_tokens: int = 16,
    ) -> None:
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not math.isfinite(threshold)
            or threshold <= 0.0
        ):
            raise ValueError("knowledge_expert_router_threshold_invalid")
        if isinstance(top_k, bool) or not isinstance(top_k, int):
            raise ValueError("knowledge_expert_router_top_k_invalid")
        if isinstance(hysteresis_tokens, bool) or not isinstance(hysteresis_tokens, int):
            raise ValueError("knowledge_expert_router_hysteresis_invalid")
        self._candidates = candidates
        self._threshold = float(threshold)
        self._top_k = max(1, min(int(top_k), 16))
        self._hysteresis_tokens = max(0, min(int(hysteresis_tokens), 4096))
        self._last_switch_token = -1

    def decide(
        self,
        *,
        query: str,
        entropy: float | None,
        token_index: int,
        generation_id: str,
        scope: Mapping[str, str],
        mode: str,
        citation_required: bool,
        capability_ready: bool,
    ) -> KnowledgeExpertRoutingDecision:
        required_scope = tuple(str(scope.get(key) or "").strip() for key in (
            "tenant_id",
            "workspace_id",
            "repository_id",
        ))
        if (
            mode not in _MODES
            or not query.strip()
            or token_index < 0
            or not generation_id.strip()
            or not all(required_scope)
        ):
            return self._decision(False, str(mode), "router_input_invalid", (), entropy, generation_id, True)
        if mode == "off":
            return self._decision(False, mode, "mode_off", (), entropy, generation_id, citation_required)
        if not capability_ready:
            return self._decision(False, mode, "runtime_capability_unavailable", (), entropy, generation_id, True)
        if entropy is None or not math.isfinite(entropy):
            return self._decision(False, mode, "entropy_unavailable", (), entropy, generation_id, True)
        if entropy <= self._threshold:
            return self._decision(False, mode, "base_model_confident", (), entropy, generation_id, citation_required)
        if self._last_switch_token >= 0 and token_index - self._last_switch_token < self._hysteresis_tokens:
            return self._decision(False, mode, "switch_hysteresis", (), entropy, generation_id, citation_required)
        admitted = [
            item
            for item in self._candidates.search(query=query, top_k=self._top_k * 3)
            if not item.revoked
            and _DIGEST.fullmatch(item.manifest_digest)
            and math.isfinite(item.score)
            and (item.tenant_id, item.workspace_id, item.repository_id)
            == required_scope
        ]
        admitted.sort(key=lambda item: (-float(item.score), item.manifest_digest))
        selected = tuple(item.manifest_digest for item in admitted[: self._top_k])
        if not selected:
            return self._decision(False, mode, "expert_candidate_unavailable", (), entropy, generation_id, True)
        self._last_switch_token = max(0, int(token_index))
        return self._decision(True, mode, "expert_selected", selected, entropy, generation_id, citation_required)

    def _decision(
        self,
        execute: bool,
        mode: str,
        reason: str,
        candidates: tuple[str, ...],
        entropy: float | None,
        generation_id: str,
        requires_rag: bool,
    ) -> KnowledgeExpertRoutingDecision:
        return KnowledgeExpertRoutingDecision(
            execute=execute,
            mode=str(mode),
            reason_code=reason,
            candidate_manifest_digests=candidates,
            entropy=entropy,
            threshold=self._threshold,
            generation_id=str(generation_id),
            requires_rag=requires_rag,
        )


__all__ = [
    "KnowledgeExpertCandidate",
    "KnowledgeExpertCandidatePort",
    "UncertaintyAwareKnowledgeExpertRouter",
]
