from __future__ import annotations

import time
from dataclasses import dataclass, field

from client_surfaces.operator_tui.visual.markdown.mermaid_renderer import MermaidRenderResult


@dataclass
class _CacheEntry:
    result: MermaidRenderResult
    cached_at: float


@dataclass
class MermaidCache:
    enabled: bool = True
    _store: dict[str, _CacheEntry] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0

    def _key(self, source_hash: str, backend: str, fmt: str, width: int, height: int) -> str:
        return f"{source_hash}|{backend}|{fmt}|{width}x{height}"

    def get(
        self,
        source_hash: str,
        backend: str,
        fmt: str,
        width: int,
        height: int,
    ) -> MermaidRenderResult | None:
        if not self.enabled:
            self.misses += 1
            return None
        entry = self._store.get(self._key(source_hash, backend, fmt, width, height))
        if entry is None:
            self.misses += 1
            return None
        self.hits += 1
        return entry.result

    def put(
        self,
        source_hash: str,
        backend: str,
        fmt: str,
        width: int,
        height: int,
        result: MermaidRenderResult,
    ) -> None:
        if not self.enabled:
            return
        self._store[self._key(source_hash, backend, fmt, width, height)] = _CacheEntry(
            result=result, cached_at=time.monotonic()
        )

    def diagnostics(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "size": len(self._store)}
