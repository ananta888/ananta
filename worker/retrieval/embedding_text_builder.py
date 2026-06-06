"""
EmbeddingTextBuilder — EPC-011

Separates the `embedding_text` composition logic from the provider resolution.

Before this module, the text fed to embed_texts() was assembled inline at call sites:
  - index_builder.py: chunk["text"]
  - codecompass_vector_store.py: doc["embedding_text"]
  - codecompass_embedding_loader.py: record["embedding_text"]

This module provides a single place to define what text is embedded, how it is
truncated, and how it is composed from structured document fields — without
touching the provider layer.
"""
from __future__ import annotations

import re
from typing import Any

# Hard cap per text to keep token budgets predictable
_MAX_CHARS = 4096
_WHITESPACE_RE = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", str(text or "")).strip()


def build_embedding_text(
    document: dict[str, Any],
    *,
    max_chars: int = _MAX_CHARS,
) -> str:
    """
    Build a single embedding text string from a normalized document dict.

    Priority order:
      1. Explicit `embedding_text` field (trust caller)
      2. Composite: symbol + path + summary + content (from text_fields if present)
      3. Plain `text` or `content` field
      4. Empty string (provider will produce a zero-similarity vector)
    """
    # 1. Explicit override
    explicit = _clean(document.get("embedding_text") or "")
    if explicit:
        return explicit[:max_chars]

    # 2. Structured text_fields (from normalize_codecompass_records)
    text_fields = document.get("text_fields")
    if isinstance(text_fields, dict):
        parts: list[str] = []
        for key in ("symbol_text", "path_text", "summary_text", "focus_text", "content_text"):
            part = _clean(text_fields.get(key) or "")
            if part and part not in parts:
                parts.append(part)
        composite = " ".join(parts)
        if composite:
            return composite[:max_chars]

    # 3. Direct text / content field
    for field in ("text", "content", "summary", "title"):
        val = _clean(document.get(field) or "")
        if val:
            return val[:max_chars]

    return ""


def build_query_embedding_text(query: str, *, max_chars: int = _MAX_CHARS) -> str:
    """Clean and truncate a query string before embedding."""
    return _clean(query)[:max_chars]


def build_embedding_texts_batch(
    documents: list[dict[str, Any]],
    *,
    max_chars: int = _MAX_CHARS,
) -> list[str]:
    """Batch version of build_embedding_text."""
    return [build_embedding_text(doc, max_chars=max_chars) for doc in list(documents or [])]
