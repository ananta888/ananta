from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from worker.retrieval.chunking import split_into_chunks
from worker.retrieval.embedding_provider import EmbeddingProvider, HashEmbeddingProvider, build_embedding_provider
from worker.retrieval.index_contract import build_index_entry
from worker.retrieval.index_state import RetrievalIndexState, compute_path_hash, derive_workspace_revision


@dataclass(frozen=True)
class DeltaSet:
    changed_paths: list[str]
    deleted_paths: list[str]
    renamed_paths: dict[str, str]


def compute_delta_set(*, previous_path_hashes: dict[str, str], files: dict[str, str]) -> DeltaSet:
    current_hashes = {str(path): compute_path_hash(content) for path, content in dict(files or {}).items()}
    previous = {str(path): str(hash_value) for path, hash_value in dict(previous_path_hashes or {}).items()}
    changed = [path for path, path_hash in current_hashes.items() if previous.get(path) != path_hash]
    deleted = [path for path in previous if path not in current_hashes]
    renamed: dict[str, str] = {}
    deleted_by_hash = {previous[path]: path for path in deleted if previous.get(path)}
    for path in changed:
        path_hash = current_hashes[path]
        old_path = deleted_by_hash.get(path_hash)
        if old_path:
            renamed[old_path] = path
    return DeltaSet(changed_paths=sorted(changed), deleted_paths=sorted(deleted), renamed_paths=renamed)


def _build_entries_for_paths(
    *,
    files: dict[str, str],
    paths: list[str],
    embedding_provider: EmbeddingProvider,
    embedding_version: str,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in list(paths or []):
        content = str(files.get(path) or "")
        chunks = split_into_chunks(path=path, content=content)
        embeddings = embedding_provider.embed_texts([chunk["text"] for chunk in chunks])
        for chunk, embedding in zip(chunks, embeddings, strict=False):
            meta = dict(chunk.get("metadata") or {})
            entries.append(
                build_index_entry(
                    path=path,
                    chunk_id=str(meta.get("chunk_id") or ""),
                    text=str(chunk.get("text") or ""),
                    embedding=list(embedding or []),
                    embedding_version=embedding_version,
                    source_hash=str(meta.get("content_hash") or ""),
                    language=str(meta.get("language") or "text"),
                    symbol_name=str(meta.get("symbol_name") or ""),
                    start_byte=int(meta.get("start_byte") or 0),
                    end_byte=int(meta.get("end_byte") or 0),
                )
            )
    return entries


def _resolve_provider_from_config_service(scope: str) -> EmbeddingProvider:
    """Use EmbeddingProviderConfigService when available; fall back to HashEmbeddingProvider."""
    try:
        from agent.services.embedding_provider_config_service import (
            EmbeddingProviderConfigService,
            build_embedding_provider_from_config,
        )
        svc = EmbeddingProviderConfigService()
        cfg = svc.resolve(scope)
        return build_embedding_provider_from_config(cfg)
    except Exception:
        return HashEmbeddingProvider()


def provider_changed_since_last_build(
    *,
    previous_state: dict[str, Any] | None,
    current_provider: EmbeddingProvider,
) -> bool:
    """EPC-012: detect provider/model change that requires a full rebuild."""
    if not previous_state or not isinstance(previous_state, dict):
        return False
    prev_model = str(previous_state.get("embedding_model_version") or "").strip()
    curr_model = str(getattr(current_provider, "model_version", "") or "").strip()
    prev_provider = str(previous_state.get("embedding_provider") or "").strip()
    curr_provider = str(getattr(current_provider, "provider_id", "") or "").strip()
    return prev_model != curr_model or prev_provider != curr_provider


def build_incremental_index(
    *,
    files: dict[str, str],
    previous_entries: list[dict[str, Any]] | None = None,
    previous_path_hashes: dict[str, str] | None = None,
    previous_state: dict[str, Any] | None = None,
    retrieval_model_version: str = "hybrid-v1",
    embedding_provider: EmbeddingProvider | None = None,
    embedding_scope: str = "worker_retrieval",
    revision_hint: str | None = None,
) -> dict[str, Any]:
    # EPC-010: use config service when no explicit provider is injected
    if embedding_provider is not None:
        provider = embedding_provider
    else:
        provider = _resolve_provider_from_config_service(embedding_scope)

    # EPC-012: force full rebuild when provider or model changed
    if provider_changed_since_last_build(
        previous_state=previous_state, current_provider=provider
    ):
        previous_entries = []
        previous_path_hashes = {}
    previous = list(previous_entries or [])
    previous_hashes = dict(previous_path_hashes or {})
    delta = compute_delta_set(previous_path_hashes=previous_hashes, files=files)
    changed_or_added = sorted(set(delta.changed_paths))
    keep_paths = {str(path) for path in files.keys()}
    remaining = [item for item in previous if str(item.get("path") or "") in keep_paths and str(item.get("path") or "") not in changed_or_added]
    new_entries = _build_entries_for_paths(
        files=files,
        paths=changed_or_added,
        embedding_provider=provider,
        embedding_version=provider.model_version,
    )
    path_hashes = {str(path): compute_path_hash(content) for path, content in dict(files or {}).items()}
    state = RetrievalIndexState(
        index_version="retrieval-index.v1",
        retrieval_model_version=str(retrieval_model_version).strip() or "hybrid-v1",
        embedding_model_version=str(provider.model_version).strip() or "hash-v1",
        workspace_revision=derive_workspace_revision(path_hashes=path_hashes, revision_hint=revision_hint),
        path_hashes=path_hashes,
    )
    entries = [*remaining, *new_entries]
    state_dict = state.as_dict()
    # EPC-012: persist provider_id so rebuild detection works across restarts
    state_dict["embedding_provider"] = str(getattr(provider, "provider_id", "") or "")
    return {
        "state": state_dict,
        "entries": entries,
        "delta": {
            "changed_paths": delta.changed_paths,
            "deleted_paths": delta.deleted_paths,
            "renamed_paths": dict(delta.renamed_paths),
        },
    }

