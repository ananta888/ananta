from __future__ import annotations

import re


def split_wiki_content(text: str, *, max_chars: int = 700) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]
    chunks: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?])\s+", normalized):
        part = sentence.strip()
        if not part:
            continue
        if not current:
            current = part
            continue
        if len(current) + 1 + len(part) <= max_chars:
            current = f"{current} {part}"
            continue
        chunks.append(current)
        current = part
    if current:
        chunks.append(current)
    return chunks

