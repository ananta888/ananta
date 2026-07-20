"""Bounded plaintext-canary scanner for release-gate storage surfaces."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Protocol, Sequence

REQUIRED_CANARY_SURFACES = frozenset(
    {"log", "db", "audit", "task", "artifact", "metric", "browserstore"}
)


class SemanticMediaCanaryScanError(RuntimeError):
    def __init__(self, reason_code: str, *, surface: str | None = None) -> None:
        self.reason_code = reason_code
        self.surface = surface
        super().__init__(reason_code)


class SemanticMediaCanarySurface(Protocol):
    @property
    def name(self) -> str: ...

    def chunks(self) -> Iterable[bytes | str]: ...


@dataclass(frozen=True, slots=True)
class SemanticMediaCanaryScanSummary:
    surface_count: int
    chunk_count: int
    scanned_bytes: int
    surface_digest: str

    def public(self) -> dict[str, int | str]:
        return {
            "surface_count": self.surface_count,
            "chunk_count": self.chunk_count,
            "scanned_bytes": self.scanned_bytes,
            "surface_digest": self.surface_digest,
        }


class SemanticMediaCanaryScanService:
    """Scans provided real adapters; it never discovers paths implicitly."""

    def __init__(self, *, maximum_chunks: int = 100_000, maximum_bytes: int = 64 * 1024 * 1024) -> None:
        if not 1 <= maximum_chunks <= 1_000_000 or not 1 <= maximum_bytes <= 1024**3:
            raise ValueError("semantic_canary_scan_bound_invalid")
        self._maximum_chunks = maximum_chunks
        self._maximum_bytes = maximum_bytes

    def scan(
        self,
        *,
        canaries: Sequence[bytes | str],
        surfaces: Sequence[SemanticMediaCanarySurface],
        required_surfaces: frozenset[str] = REQUIRED_CANARY_SURFACES,
    ) -> SemanticMediaCanaryScanSummary:
        needles = tuple(_needle(value) for value in canaries)
        if not needles or any(len(value) < 8 or len(value) > 4096 for value in needles):
            raise SemanticMediaCanaryScanError("semantic_canary_invalid")
        names = [surface.name for surface in surfaces]
        if len(names) != len(set(names)) or set(names) != set(required_surfaces):
            raise SemanticMediaCanaryScanError("semantic_canary_surface_coverage_invalid")
        chunks = 0
        scanned_bytes = 0
        digest = hashlib.sha256()
        for surface in sorted(surfaces, key=lambda item: item.name):
            digest.update(surface.name.encode("utf-8"))
            digest.update(b"\0")
            for raw in surface.chunks():
                value = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
                chunks += 1
                scanned_bytes += len(value)
                if chunks > self._maximum_chunks or scanned_bytes > self._maximum_bytes:
                    raise SemanticMediaCanaryScanError("semantic_canary_scan_bound_exceeded")
                if any(needle in value for needle in needles):
                    raise SemanticMediaCanaryScanError(
                        "semantic_plaintext_canary_detected",
                        surface=surface.name,
                    )
                digest.update(hashlib.sha256(value).digest())
        return SemanticMediaCanaryScanSummary(len(names), chunks, scanned_bytes, digest.hexdigest())


def _needle(value: bytes | str) -> bytes:
    return value.encode("utf-8") if isinstance(value, str) else bytes(value)


__all__ = [
    "REQUIRED_CANARY_SURFACES",
    "SemanticMediaCanaryScanError",
    "SemanticMediaCanaryScanService",
    "SemanticMediaCanaryScanSummary",
    "SemanticMediaCanarySurface",
]
