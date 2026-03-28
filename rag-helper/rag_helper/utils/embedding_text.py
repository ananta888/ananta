from __future__ import annotations


def build_embedding_text(mode: str, verbose: str, compact: str | None = None) -> str:
    if mode == "compact":
        return compact or verbose
    return verbose


def compact_list(items: list[str], limit: int = 5) -> str:
    if not items:
        return "none"
    return ", ".join(items[:limit])


def compact_text(text: str, limit: int = 160) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized or "none"
    return normalized[: max(0, limit - 3)].rstrip() + "..."
