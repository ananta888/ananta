from __future__ import annotations

from collections import defaultdict
from typing import Any

from agent.services.rag_policy_service import SENSITIVITY_CLASSES, is_chunk_allowed_for_scope, normalize_llm_scope

_SOURCE_CLASS_ORDER = ["repo_code", "internal_docs", "task_memory", "offline_wiki", "external_research", "unknown"]
_SEGREGATION_MATRIX: dict[str, set[str]] = {
    "repo_code": {"repo_code", "offline_wiki"},
    "offline_wiki": {"offline_wiki", "repo_code"},
    "internal_docs": {"internal_docs"},
    "task_memory": {"task_memory"},
    "external_research": {"external_research"},
    "unknown": {"unknown"},
}


class RetrievalPolicyFilterService:
    """Policy filter for retrieval chunks before prompt/context assembly."""

    def apply_filter(
        self,
        *,
        chunks: list[dict[str, Any]],
        llm_scope: str,
        policy_mode: str = "standard",
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        scope = normalize_llm_scope(llm_scope)
        normalized_mode = str(policy_mode or "standard").strip().lower() or "standard"
        decisions: list[dict[str, Any]] = []
        retained: list[dict[str, Any]] = []
        denied_by_reason: dict[str, int] = defaultdict(int)
        downgraded_by_reason: dict[str, int] = defaultdict(int)
        source_classes_before: dict[str, int] = defaultdict(int)

        for item in list(chunks or []):
            chunk = dict(item or {})
            metadata = dict(chunk.get("metadata") or {})
            source_class = self._source_class(metadata)
            source_classes_before[source_class] += 1
            unknown_sensitivity = self._is_unknown_sensitivity(metadata)

            if unknown_sensitivity and scope in {"external_cloud_allowed", "trusted_private_cloud"}:
                reason = "unknown_sensitivity_default_deny"
                denied_by_reason[reason] += 1
                decisions.append(self._decision(chunk=chunk, source_class=source_class, decision="denied", reason=reason, metadata=metadata))
                continue

            allowed, reason = is_chunk_allowed_for_scope(chunk={"metadata": metadata}, llm_scope=scope)
            if allowed:
                retained.append(chunk)
                decisions.append(self._decision(chunk=chunk, source_class=source_class, decision="allowed", reason=reason, metadata=metadata))
                continue

            if reason == "raw_not_allowed_for_external_scope":
                downgraded = self._downgrade_chunk(chunk)
                retained.append(downgraded)
                downgraded_by_reason[reason] += 1
                decisions.append(self._decision(chunk=downgraded, source_class=source_class, decision="downgraded", reason=reason, metadata=metadata))
                continue

            denied_by_reason[reason] += 1
            decisions.append(self._decision(chunk=chunk, source_class=source_class, decision="denied", reason=reason, metadata=metadata))

        retained, segregation_meta = self._apply_source_segregation(
            chunks=retained,
            decisions=decisions,
            denied_by_reason=denied_by_reason,
            scope=scope,
            policy_mode=normalized_mode,
        )

        source_classes_after: dict[str, int] = defaultdict(int)
        for item in retained:
            source_classes_after[self._source_class(dict(item.get("metadata") or {}))] += 1

        diagnostics = {
            "policy_version": "retrieval_policy_filter.v1",
            "scope": scope,
            "policy_mode": normalized_mode,
            "input_count": len(chunks or []),
            "allowed_count": len(retained),
            "denied_count": int(sum(denied_by_reason.values())),
            "downgraded_count": int(sum(downgraded_by_reason.values())),
            "denied_by_reason": dict(sorted(denied_by_reason.items(), key=lambda item: item[0])),
            "downgraded_by_reason": dict(sorted(downgraded_by_reason.items(), key=lambda item: item[0])),
            "source_class_contributions_before": dict(sorted(source_classes_before.items(), key=lambda item: item[0])),
            "source_class_contributions_after": dict(sorted(source_classes_after.items(), key=lambda item: item[0])),
            "segregation": segregation_meta,
            "decisions": decisions[:80],
        }
        return retained, diagnostics

    def _apply_source_segregation(
        self,
        *,
        chunks: list[dict[str, Any]],
        decisions: list[dict[str, Any]],
        denied_by_reason: dict[str, int],
        scope: str,
        policy_mode: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if scope == "local_only":
            return chunks, {"applied": False, "anchor_source_class": None, "allowed_source_classes": [], "reason": "local_scope_allows_mixing"}
        if policy_mode not in {"standard", "strict"}:
            return chunks, {"applied": False, "anchor_source_class": None, "allowed_source_classes": [], "reason": "policy_mode_no_segregation"}
        if not chunks:
            return chunks, {"applied": True, "anchor_source_class": None, "allowed_source_classes": [], "reason": "no_candidates"}

        ranked = sorted(
            chunks,
            key=lambda item: (
                -float(item.get("score") or 0.0),
                self._source_class_rank(self._source_class(dict(item.get("metadata") or {}))),
                str(item.get("source") or ""),
            ),
        )
        anchor = self._source_class(dict(ranked[0].get("metadata") or {}))
        allowed_classes = _SEGREGATION_MATRIX.get(anchor, {anchor})

        selected: list[dict[str, Any]] = []
        for item in chunks:
            source_class = self._source_class(dict(item.get("metadata") or {}))
            if source_class in allowed_classes:
                selected.append(item)
                continue
            reason = f"source_segregation_blocked:{source_class}_with_{anchor}"
            denied_by_reason[reason] += 1
            decisions.append(
                self._decision(
                    chunk=item,
                    source_class=source_class,
                    decision="denied",
                    reason=reason,
                    metadata=dict(item.get("metadata") or {}),
                )
            )

        return selected, {
            "applied": True,
            "anchor_source_class": anchor,
            "allowed_source_classes": sorted(allowed_classes),
            "reason": "scope_source_segregation_enforced",
        }

    def _source_class(self, metadata: dict[str, Any]) -> str:
        source_origin = str(metadata.get("source_origin") or metadata.get("source_type") or "").strip().lower()
        source_type = str(metadata.get("source_type") or "").strip().lower()
        if source_origin == "external_research":
            return "external_research"
        if source_type == "task_memory":
            return "task_memory"
        if source_type == "wiki" or source_origin == "wiki":
            return "offline_wiki"
        if source_type == "artifact":
            return "internal_docs"
        if source_type == "repo":
            return "repo_code"
        return "unknown"

    @staticmethod
    def _is_unknown_sensitivity(metadata: dict[str, Any]) -> bool:
        raw = str(metadata.get("sensitivity") or "").strip().lower()
        return raw not in SENSITIVITY_CLASSES

    @staticmethod
    def _downgrade_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(chunk.get("metadata") or {})
        downgraded_metadata = {
            **metadata,
            "policy_filter": {
                **dict(metadata.get("policy_filter") or {}),
                "decision": "downgraded",
                "reason": "raw_not_allowed_for_external_scope",
            },
        }
        return {
            **chunk,
            "content": "[POLICY_DOWNGRADED] Raw content removed by retrieval policy filter.",
            "metadata": downgraded_metadata,
            "score": float(chunk.get("score") or 0.0) * 0.5,
        }

    def _source_class_rank(self, source_class: str) -> int:
        try:
            return _SOURCE_CLASS_ORDER.index(source_class)
        except ValueError:
            return len(_SOURCE_CLASS_ORDER)

    def _decision(self, *, chunk: dict[str, Any], source_class: str, decision: str, reason: str, metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            "source": str(chunk.get("source") or ""),
            "engine": str(chunk.get("engine") or ""),
            "decision": decision,
            "reason": reason,
            "source_class": source_class,
            "sensitivity": str(metadata.get("sensitivity") or ""),
            "classification": str(metadata.get("classification") or ""),
        }


_service = RetrievalPolicyFilterService()


def get_retrieval_policy_filter_service() -> RetrievalPolicyFilterService:
    return _service
