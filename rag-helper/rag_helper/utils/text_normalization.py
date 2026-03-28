from __future__ import annotations

import re


def normalize_ws(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    lines = [x for x in lines if x]
    return "\n".join(lines)


def compact_code_snippet(text: str, max_len: int = 2200) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if len(text) > max_len:
        return text[:max_len] + "\n...[truncated]..."
    return text
