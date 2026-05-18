from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Optional

from agent.services.rag_policy_service import (
    SENSITIVITY_CLASSES,
    is_chunk_allowed_for_scope,
    normalize_sensitivity,
)
from agent.services.workspace_context_policy import WorkspaceContextPolicy

_SENSITIVITY_ORDER = [
    "public",
    "internal_low",
    "internal_medium",
    "internal_high",
    "confidential",
    "secret",
    "credential",
    "customer_data",
    "legal",
    "security_sensitive",
]
_SENSITIVITY_RANK: dict[str, int] = {s: i for i, s in enumerate(_SENSITIVITY_ORDER)}


def _sensitivity_rank(value: str) -> int:
    return _SENSITIVITY_RANK.get(normalize_sensitivity(value), len(_SENSITIVITY_ORDER))


def _ceiling_rank(ceiling: str) -> int:
    return _SENSITIVITY_RANK.get(normalize_sensitivity(ceiling), len(_SENSITIVITY_ORDER))


def provider_to_llm_scope(provider: str, base_url: Optional[str]) -> str:
    p = str(provider or "").strip().lower()
    url = str(base_url or "").strip().lower()

    if p in ("ollama", "lmstudio") or any(
        h in url for h in ("localhost", "127.0.0.1", "0.0.0.0", "::1")
    ):
        return "local_only"
    if p in ("openai", "anthropic", "gemini", "cohere", "mistral"):
        return "external_cloud_allowed"
    if url:
        return "trusted_private_cloud"
    return "external_cloud_allowed"


@dataclass
class ContextFileSelection:
    selected_paths: list[str] = field(default_factory=list)
    excluded_paths: list[str] = field(default_factory=list)
    exclusion_reasons: dict[str, str] = field(default_factory=dict)
    total_chunks_evaluated: int = 0


class ContextFileSelector:
    def select(
        self,
        chunks: list[dict],
        policy: WorkspaceContextPolicy,
        llm_scope: str,
    ) -> ContextFileSelection:
        result = ContextFileSelection(total_chunks_evaluated=len(chunks))

        seen: dict[str, dict] = {}
        for chunk in chunks:
            path = str(chunk.get("path") or chunk.get("file_path") or "")
            if path and path not in seen:
                seen[path] = chunk

        ceiling_rank = _ceiling_rank(policy.sensitivity_ceiling)

        for path, chunk in seen.items():
            # Normalize sensitivity from either top-level or nested metadata field
            raw_sens = chunk.get("sensitivity") or (chunk.get("metadata") or {}).get("sensitivity")
            chunk_sensitivity = normalize_sensitivity(raw_sens or "public")

            # Ensure is_chunk_allowed_for_scope sees consistent sensitivity via metadata
            scope_chunk = {**chunk, "metadata": {**(chunk.get("metadata") or {}), "sensitivity": chunk_sensitivity}}
            allowed_by_scope, _ = is_chunk_allowed_for_scope(chunk=scope_chunk, llm_scope=llm_scope)
            if not allowed_by_scope:
                result.excluded_paths.append(path)
                result.exclusion_reasons[path] = "sensitivity_blocked"
                continue
            if _sensitivity_rank(chunk_sensitivity) > ceiling_rank:
                result.excluded_paths.append(path)
                result.exclusion_reasons[path] = "ceiling_exceeded"
                continue

            if policy.allowed_paths:
                matched = any(fnmatch.fnmatch(path, pat) for pat in policy.allowed_paths)
                if not matched:
                    result.excluded_paths.append(path)
                    result.exclusion_reasons[path] = "path_not_allowed"
                    continue

            result.selected_paths.append(path)

        if policy.max_files > 0 and len(result.selected_paths) > policy.max_files:
            overflow = result.selected_paths[policy.max_files:]
            result.selected_paths = result.selected_paths[: policy.max_files]
            for p in overflow:
                result.excluded_paths.append(p)
                result.exclusion_reasons[p] = "max_files_exceeded"

        return result


_instance: Optional[ContextFileSelector] = None


def get_context_file_selector() -> ContextFileSelector:
    global _instance
    if _instance is None:
        _instance = ContextFileSelector()
    return _instance
