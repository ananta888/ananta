from __future__ import annotations

import hashlib


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def safe_id(*parts: str) -> str:
    return sha1_text("::".join(parts))[:16]
