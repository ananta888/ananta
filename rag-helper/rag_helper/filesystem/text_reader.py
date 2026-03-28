from __future__ import annotations

from pathlib import Path


def read_text_file(path: Path) -> str | None:
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            return path.read_text(encoding=enc)
        except Exception:
            pass
    return None
