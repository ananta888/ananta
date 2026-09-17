"""Worker-side CodeCompass context lookup for the companion.

The media worker never holds the knowledge index nor user credentials: it
asks the Hub over the scoped worker key for a small, bounded context block.
"""

import json
import os
import urllib.request

from worker.meet_media.contract import encode, load_key, signature

RETRIEVE_URL = os.environ.get(
    "MEET_HUB_ASSIST_URL",
    "http://meet-authorizing-hub:5000/api/meet/v1/internal/assist/retrieve",
)
MAX_CONTEXT_CHARS = int(os.environ.get("MEET_ASSIST_MAX_CHARS", "1400"))


def fetch_snippets(query, *, limit=5, timeout=10):
    key = load_key(os.environ["MEET_WORKER_KEY_FILE"])
    body = encode({"query": query, "limit": limit})
    request = urllib.request.Request(
        RETRIEVE_URL,
        body,
        {
            "Content-Type": "application/json",
            "X-Ananta-Task-Signature": signature(key, body),
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    snippets = payload.get("snippets") if isinstance(payload, dict) else None
    return [_snippet(item) for item in (snippets or []) if isinstance(item, dict)]


def _snippet(item):
    """Keep only the bounded fields the companion consumes (path, symbol, revision)."""
    return {
        "path": str(item.get("path") or "").strip()[:512],
        "symbol": str(item.get("symbol") or "").strip()[:256],
        "revision": str(item.get("revision") or "").strip()[:64],
        "score": item.get("score") if isinstance(item.get("score"), (int, float)) else None,
        "excerpt": str(item.get("excerpt") or "")[:1200],
    }


def build_context(query, *, limit=5, max_chars=None):
    """Return a bounded, plain-text project-context block (or empty string)."""
    budget = MAX_CONTEXT_CHARS if max_chars is None else int(max_chars)
    if not query or budget <= 0:
        return ""
    try:
        snippets = fetch_snippets(query, limit=limit)
    except Exception:
        return ""
    parts = []
    used = 0
    for item in snippets:
        path = str(item.get("path") or "").strip()
        excerpt = " ".join(str(item.get("excerpt") or "").split())
        if not excerpt:
            continue
        remaining = budget - used
        if remaining <= 0:
            break
        block = f"[{path}] {excerpt}" if path else excerpt
        if len(block) > remaining:
            block = block[:remaining]
        parts.append(block)
        used += len(block)
    return "\n".join(parts)
