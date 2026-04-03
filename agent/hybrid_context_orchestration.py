from __future__ import annotations

from typing import Any, Callable


def collect_context_chunks(
    *,
    query: str,
    quotas: dict[str, int],
    repository_engine: Any,
    semantic_engine: Any,
    agentic_engine: Any,
) -> list[Any]:
    chunks: list[Any] = []
    if quotas.get("repository_map", 0) > 0:
        chunks.extend(repository_engine.search(query, top_k=quotas["repository_map"]))
    if quotas.get("semantic_search", 0) > 0:
        chunks.extend(semantic_engine.search(query, top_k=quotas["semantic_search"]))
    if quotas.get("agentic_search", 0) > 0:
        chunks.extend(agentic_engine.search(query, top_k=quotas["agentic_search"]))
    return chunks


def serialize_context_result(
    *,
    query: str,
    quotas: dict[str, int],
    policy_version: str,
    chunks: list[Any],
    redact: Callable[[str], str],
    estimate_tokens: Callable[[str], int],
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
    }
