from __future__ import annotations

from pathlib import Path
from typing import Protocol, Any, Iterable


class WikiDumpParser(Protocol):
    def iter_items(self, *, corpus_path: Path, index_path: Path | None = None) -> Iterable[dict[str, Any]]:
        ...

