from __future__ import annotations

from typing import Any, Callable
from types import SimpleNamespace


def collect_context_chunks(
    *,
    query: str,
    quotas: dict[str, int],
    repository_engine: Any,
    semantic_engine: Any,
    agentic_engine: Any,
    codecompass_vector_service: Any | None = None,
    allowed_paths: list[str] | None = None,
) -> list[Any]:
    # CCRDS: allowed_paths narrows repository/agentic search at the source;
    # semantic results are filtered downstream by the DomainScopeFilter.
    chunks: list[Any] = []
    if quotas.get("repository_map", 0) > 0:
        chunks.extend(
            repository_engine.search(query, top_k=quotas["repository_map"], allowed_paths=allowed_paths)
        )
    if quotas.get("codecompass_vector", 0) > 0 and codecompass_vector_service is not None:
        rows = codecompass_vector_service.search(
            query=query,
            top_k=quotas["codecompass_vector"],
            allowed_paths=allowed_paths,
        )
        chunks.extend(_coerce_vector_chunks(rows))
    if quotas.get("semantic_search", 0) > 0:
        chunks.extend(semantic_engine.search(query, top_k=quotas["semantic_search"]))
    if quotas.get("agentic_search", 0) > 0:
        chunks.extend(
            agentic_engine.search(query, top_k=quotas["agentic_search"], allowed_paths=allowed_paths)
        )
    return chunks


def _coerce_vector_chunks(rows: list[Any]) -> list[Any]:
    chunks: list[Any] = []
    for row in rows:
        if not isinstance(row, dict):
            chunks.append(row)
            continue
        metadata = dict(row.get("metadata") or {})
        chunks.append(
            SimpleNamespace(
                engine=str(row.get("engine") or "codecompass_vector"),
                source=str(row.get("source") or metadata.get("file") or ""),
                content=str(row.get("content") or ""),
                score=float(row.get("score") or 0.0),
                metadata=metadata,
            )
        )
    return chunks


def serialize_context_result(
    *,
    query: str,
    quotas: dict[str, int],
    policy_version: str,
    chunks: list[Any],
    redact: Callable[[str], str],
    estimate_tokens: Callable[[str], int],
    retrieval_diagnostics: dict[str, Any] | None = None,
) -> dict[str, object]:
    serialized_chunks = []
    context_lines: list[str] = []
    for chunk in chunks:
        safe_content = redact(chunk.content)
        context_lines.append(f"[{chunk.engine}] {chunk.source}\n{safe_content}")
        serialized_chunks.append(
            {
                "engine": chunk.engine,
                "source": chunk.source,
                "score": round(chunk.score, 3),
                "content": safe_content,
                "metadata": chunk.metadata,
            }
        )

    context_text = "\n\n".join(context_lines)
    return {
        "query": query,
        "strategy": quotas,
        "policy_version": policy_version,
        "chunks": serialized_chunks,
        "context_text": context_text,
        "token_estimate": estimate_tokens(context_text),
        "retrieval_diagnostics": dict(retrieval_diagnostics or {}),
    }
