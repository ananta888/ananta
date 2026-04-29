from __future__ import annotations

_SYNONYMS = {
    "bug": ("defect", "failure", "issue"),
    "fix": ("repair", "resolve"),
    "auth": ("authentication", "authorization"),
    "repo": ("repository", "workspace"),
    "llm": ("model", "inference"),
}


def rewrite_query(query: str) -> dict[str, str]:
    original = str(query or "").strip()
    if not original:
        return {"original": "", "rewritten": ""}
    tokens = original.split()
    expansions: list[str] = []
    for token in tokens:
        normalized = token.strip().lower()
        expansions.extend(_SYNONYMS.get(normalized, ()))
    rewritten = " ".join([original, *expansions]).strip()
    return {"original": original, "rewritten": rewritten}

